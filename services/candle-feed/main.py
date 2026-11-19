"""candle-feed — bridges the MT5 terminal to Redis.

Startup sequence:
  1. Fetch last HISTORY_COUNT candles from mt5-bridge REST (GET /candles)
  2. Store in Redis List  ``candles:{symbol}:{timeframe}:history``  (LTRIM to HISTORY_COUNT)
  3. Publish each historical candle to Redis PubSub ``candles:{symbol}:{timeframe}``
  4. Connect to mt5-bridge WebSocket  ws://{bridge}/ws/candles
  5. Each new closed candle → push to List + publish to PubSub

Reconnect: exponential backoff 1 → 2 → 4 → 8 → 16 → 30 s max.
After every reconnect the full historical seed runs again so the List is
up-to-date and strategy containers that join late get a full window.

Environment variables (set via .env or docker-compose):
  MT5_BRIDGE_URL    http://host.docker.internal:8000
  REDIS_URL         redis://redis:6379
  SYMBOL            XAUUSD
  TIMEFRAME         M5
  HISTORY_COUNT     500
  LOG_LEVEL         INFO
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import redis as redis_lib
import requests
import websockets
from dotenv import load_dotenv

# shared package is copied into /app/shared by the Dockerfile
sys.path.insert(0, "/app")
from shared.channels import CANDLE_CHANNEL, HEARTBEAT

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("candle-feed")

# ── Config ────────────────────────────────────────────────────────────────────

BRIDGE_HTTP_URL  = os.getenv("MT5_BRIDGE_URL", "http://host.docker.internal:8000").rstrip("/")
REDIS_URL        = os.getenv("REDIS_URL", "redis://redis:6379")
SYMBOL           = os.getenv("SYMBOL", "XAUUSD")
TIMEFRAME        = os.getenv("TIMEFRAME", "M5")
HISTORY_COUNT    = int(os.getenv("HISTORY_COUNT", "500"))
HEARTBEAT_INTERVAL = 10   # seconds

# Derive WebSocket URL from HTTP URL (http → ws, https → wss)
_parsed = urlparse(BRIDGE_HTTP_URL)
_ws_scheme = "wss" if _parsed.scheme == "https" else "ws"
BRIDGE_WS_URL = f"{_ws_scheme}://{_parsed.netloc}/ws/candles?symbol={SYMBOL}&timeframe={TIMEFRAME}"

CANDLE_PUB_CHANNEL  = CANDLE_CHANNEL.format(symbol=SYMBOL, timeframe=TIMEFRAME)
CANDLE_HISTORY_KEY  = f"candles:{SYMBOL}:{TIMEFRAME}:history"   # Redis List
HEARTBEAT_CHANNEL   = HEARTBEAT.format(service="candle-feed")

# ── Redis client (sync — publish is fast, no need for async) ─────────────────

_redis: redis_lib.Redis | None = None


def get_redis() -> redis_lib.Redis:
    global _redis
    if _redis is None:
        _redis = redis_lib.from_url(REDIS_URL, decode_responses=True)
    return _redis


# ── Publish helpers ───────────────────────────────────────────────────────────

def _publish_candle(candle_dict: dict, candle_type: str) -> None:
    """Push candle to Redis List and publish to PubSub channel."""
    r = get_redis()
    # Strip bridge-side type/ping fields before storing
    clean = {k: v for k, v in candle_dict.items() if k != "type"}
    payload = json.dumps(clean)

    # Keep a capped history list so strategy containers can seed on startup
    r.rpush(CANDLE_HISTORY_KEY, payload)
    r.ltrim(CANDLE_HISTORY_KEY, -HISTORY_COUNT, -1)

    # Live broadcast on PubSub
    r.publish(CANDLE_PUB_CHANNEL, payload)
    log.debug("Published %s candle ts=%s", candle_type, clean.get("timestamp"))


# ── Historical seed ───────────────────────────────────────────────────────────

def seed_historical() -> int:
    """Fetch last HISTORY_COUNT candles from bridge REST and populate Redis.

    Runs synchronously — called from asyncio via run_in_executor.
    Returns number of candles seeded.
    """
    url = f"{BRIDGE_HTTP_URL}/candles"
    params = {"symbol": SYMBOL, "timeframe": TIMEFRAME, "count": HISTORY_COUNT}
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.error("Historical seed failed: %s", exc)
        return 0

    candles = resp.json()
    if not candles:
        log.warning("Bridge returned 0 historical candles for %s %s", SYMBOL, TIMEFRAME)
        return 0

    r = get_redis()
    # Reset the history list then push all historical candles in order
    r.delete(CANDLE_HISTORY_KEY)
    for c in candles:
        payload = json.dumps(c)
        r.rpush(CANDLE_HISTORY_KEY, payload)
        # Publish historical candles to PubSub as well (type="historical")
        # so any already-running strategy containers can receive them
        r.publish(CANDLE_PUB_CHANNEL, payload)

    log.info(
        "Historical seed: %d candles for %s %s (oldest=%s newest=%s)",
        len(candles), SYMBOL, TIMEFRAME,
        candles[0].get("timestamp", "?"),
        candles[-1].get("timestamp", "?"),
    )
    return len(candles)


# ── WebSocket loop ────────────────────────────────────────────────────────────

async def ws_loop() -> None:
    """Connect to bridge WS and stream live candles into Redis.

    Exits on any exception — the caller handles reconnect.
    """
    log.info("Connecting to bridge WebSocket: %s", BRIDGE_WS_URL)
    async with websockets.connect(
        BRIDGE_WS_URL,
        ping_interval=30,
        ping_timeout=15,
        close_timeout=10,
    ) as ws:
        log.info("WebSocket connected")
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            # Bridge sends {"type": "ping"} keepalives — ignore them
            if msg.get("type") == "ping":
                continue

            # Validate required candle fields before publishing
            if not all(k in msg for k in ("symbol", "timestamp", "open", "close")):
                log.warning("Unexpected WS message shape: %s", list(msg.keys()))
                continue

            _publish_candle(msg, "live")


# ── Heartbeat (background thread) ─────────────────────────────────────────────

def _heartbeat_thread() -> None:
    r = get_redis()
    while True:
        try:
            r.publish(
                HEARTBEAT_CHANNEL,
                json.dumps({"service": "candle-feed", "ts": datetime.now(tz=timezone.utc).isoformat()}),
            )
        except Exception as exc:
            log.warning("Heartbeat publish failed: %s", exc)
        time.sleep(HEARTBEAT_INTERVAL)


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    log.info(
        "candle-feed starting | bridge=%s | redis=%s | %s %s",
        BRIDGE_HTTP_URL, REDIS_URL, SYMBOL, TIMEFRAME,
    )

    # Start heartbeat daemon thread
    hb = threading.Thread(target=_heartbeat_thread, daemon=True, name="heartbeat")
    hb.start()

    loop = asyncio.get_event_loop()
    delay = 1
    max_delay = 30

    while True:
        # Seed historical candles before each WebSocket connection attempt
        # (covers both first start and gap-fill after reconnect)
        seeded = await loop.run_in_executor(None, seed_historical)
        if seeded == 0:
            log.warning("Bridge not ready yet — retrying in %ds", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)
            continue

        try:
            await ws_loop()
            # ws_loop exited cleanly (server closed connection)
            log.warning("WebSocket closed by server — reconnecting in %ds", delay)
        except (websockets.ConnectionClosed, websockets.WebSocketException) as exc:
            log.warning("WebSocket error: %s — reconnecting in %ds", exc, delay)
        except Exception as exc:
            log.error("Unexpected error in ws_loop: %s — reconnecting in %ds", exc, delay)

        await asyncio.sleep(delay)
        delay = min(delay * 2, max_delay)
        # Reset delay after a long successful run would be misleading here —
        # keep accumulating until we get a good reconnect, then reset below
        # (delay resets only after a successful seed above)


if __name__ == "__main__":
    asyncio.run(main())
