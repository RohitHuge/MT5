"""executor — receives trade signals from signals:all and places real MT5 orders.

Responsibilities:
  1. Subscribe to Redis ``signals:all`` — for each signal, query the DB to find
     which strategy currently has mode='live'; only place a real order if
     signal.strategy matches the live strategy name.
  2. Subscribe to Redis ``candles:XAUUSD:M5`` — sync open positions on each candle
  3. Write all trade events to Postgres via shared/db.py
  4. Publish heartbeat every 10 seconds

Signal flow:
  strategy-container → signals:all → executor
    → db.get_all_strategies() → find live strategy
    → if signal.strategy == live_strategy: POST /order → MT5
    → else: DEBUG log and skip

Position sync flow (every closed candle):
  executor → GET /positions → compare with _tracked_tickets
  → tickets gone from MT5 = SL/TP hit → mark closed in DB

Environment variables:
  MT5_BRIDGE_URL    http://host.docker.internal:8000
  REDIS_URL         redis://redis:6379
  POSTGRES_URL      postgresql://trader:...@postgres:5432/mt5
  SYMBOL            XAUUSD
  TIMEFRAME         M5
  LOG_LEVEL         INFO
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any

import psycopg2
import psycopg2.extras
import redis as redis_lib
import requests
from dotenv import load_dotenv

sys.path.insert(0, "/app")
from shared.channels import CANDLE_CHANNEL, HEARTBEAT, SIGNAL_ALL
from shared.db import get_all_strategies, upsert_real_trade
from shared.models import Signal

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("executor")

# ── Config ────────────────────────────────────────────────────────────────────

BRIDGE_URL         = os.getenv("MT5_BRIDGE_URL", "http://host.docker.internal:8000").rstrip("/")
REDIS_URL          = os.getenv("REDIS_URL", "redis://redis:6379")
POSTGRES_URL       = os.getenv("POSTGRES_URL", "")
SYMBOL             = os.getenv("SYMBOL", "XAUUSD")
TIMEFRAME          = os.getenv("TIMEFRAME", "M5")
HEARTBEAT_INTERVAL = 10   # seconds

CANDLE_SUB_CHANNEL = CANDLE_CHANNEL.format(symbol=SYMBOL, timeframe=TIMEFRAME)
HEARTBEAT_CHANNEL  = HEARTBEAT.format(service="executor")

# ── In-memory position tracker ────────────────────────────────────────────────
# ticket (int) → dict with trade metadata for all positions we placed
# Rebuilt from DB on startup.  Protected by _tracker_lock.

_tracked_tickets: dict[int, dict] = {}
_tracker_lock = threading.Lock()


# ── Bridge HTTP helpers ───────────────────────────────────────────────────────

def _bridge_get(path: str, timeout: int = 10) -> Any:
    resp = requests.get(f"{BRIDGE_URL}{path}", timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _bridge_post(path: str, body: dict, timeout: int = 10) -> Any:
    resp = requests.post(f"{BRIDGE_URL}{path}", json=body, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


# ── Signal handler ────────────────────────────────────────────────────────────

def _get_live_strategy_name() -> str | None:
    """Query the DB and return the name of the currently live strategy, or None.

    Returns None if no strategy has mode='live' (safe default: execute nothing).
    DB errors are logged and treated as None so the platform does not place orders
    blindly when the DB is unreachable.
    """
    try:
        strategies = get_all_strategies()
    except Exception as exc:
        log.error("DB query for live strategy failed: %s — skipping signal", exc)
        return None
    for s in strategies:
        if s.get("mode") == "live":
            return s["name"]
    return None


def handle_signal(raw: str) -> None:
    """Parse a Signal from signals:all, gate on DB live strategy, place order."""
    try:
        sig = Signal.from_json(raw)
    except Exception as exc:
        log.error("Could not parse signal JSON: %s | raw=%s", exc, raw[:200])
        return

    # Validate required fields
    if sig.direction not in ("BUY", "SELL"):
        log.error("Invalid signal direction %r — skipping", sig.direction)
        return
    if sig.lots <= 0 or sig.sl_price <= 0 or sig.tp_price <= 0:
        log.error("Signal has invalid lot/sl/tp values — skipping: %s", raw[:200])
        return

    # DB gate — only execute the signal if this strategy is currently live
    live_strategy = _get_live_strategy_name()
    if live_strategy is None:
        log.debug(
            "No live strategy in DB — skipping signal from %s (%s %s)",
            sig.strategy, sig.direction, sig.symbol,
        )
        return
    if sig.strategy != live_strategy:
        log.debug(
            "Signal from %s skipped — live strategy is %s",
            sig.strategy, live_strategy,
        )
        return

    log.info(
        "Signal accepted (live=%s): %s %s %s lots=%.2f sl=%.5f tp=%.5f",
        live_strategy, sig.direction, sig.symbol, sig.lots,
        sig.sl_price, sig.tp_price,
    )

    # Place order via bridge
    order_body = {
        "symbol":    sig.symbol,
        "direction": sig.direction,
        "lots":      sig.lots,
        "sl":        sig.sl_price,
        "tp":        sig.tp_price,
        "comment":   sig.strategy[:31],   # MT5 comment = strategy name for filtering
    }
    try:
        result = _bridge_post("/order", order_body)
    except requests.RequestException as exc:
        log.error("Bridge /order request failed: %s | signal=%s", exc, sig.to_json())
        return

    # HTTP 200 always — check error field
    if result.get("error"):
        log.warning(
            "Order rejected by MT5: %s | strategy=%s direction=%s",
            result["error"], sig.strategy, sig.direction,
        )
        return

    ticket = result["ticket"]
    fill_price = result["fill_price"]
    filled_at = datetime.fromisoformat(result["filled_at"])
    if filled_at.tzinfo is None:
        filled_at = filled_at.replace(tzinfo=timezone.utc)

    log.info(
        "Order filled: ticket=%d @ %.5f | strategy=%s",
        ticket, fill_price, sig.strategy,
    )

    trade = {
        "ticket":     ticket,
        "strategy":   sig.strategy,
        "symbol":     sig.symbol,
        "direction":  sig.direction,
        "lots":       sig.lots,
        "open_price": fill_price,
        "sl_price":   sig.sl_price,
        "tp_price":   sig.tp_price,
        "opened_at":  filled_at,
        "is_open":    True,
    }
    try:
        upsert_real_trade(trade)
    except Exception as exc:
        log.error("DB write failed for ticket=%d: %s", ticket, exc)
        # Don't return — still track in memory so sync can close it later

    with _tracker_lock:
        _tracked_tickets[ticket] = {
            "strategy":  sig.strategy,
            "symbol":    sig.symbol,
            "direction": sig.direction,
            "lots":      sig.lots,
            "fill_price": fill_price,
            "sl_price":  sig.sl_price,
            "tp_price":  sig.tp_price,
            "opened_at": filled_at,
        }


# ── Position sync ─────────────────────────────────────────────────────────────

def sync_positions() -> None:
    """Compare bridge /positions against _tracked_tickets.

    - Positions still open  → update gross_pnl in DB (upsert is_open=True)
    - Positions gone        → closed by MT5 (SL/TP hit) → mark is_open=False
    """
    try:
        positions = _bridge_get("/positions")
    except requests.RequestException as exc:
        log.warning("Could not fetch positions from bridge: %s", exc)
        return

    live_tickets = {int(p["ticket"]): p for p in positions}

    with _tracker_lock:
        tracked_copy = dict(_tracked_tickets)

    closed_tickets: list[int] = []

    for ticket, meta in tracked_copy.items():
        if ticket in live_tickets:
            # Still open — sync current profit
            pos = live_tickets[ticket]
            trade = {
                "ticket":     ticket,
                "strategy":   meta["strategy"],
                "symbol":     meta["symbol"],
                "direction":  meta["direction"],
                "lots":       meta["lots"],
                "open_price": meta["fill_price"],
                "sl_price":   meta.get("sl_price"),
                "tp_price":   meta.get("tp_price"),
                "opened_at":  meta["opened_at"],
                "gross_pnl":  pos.get("profit"),
                "commission": pos.get("commission"),
                "swap":       pos.get("swap"),
                "is_open":    True,
            }
            try:
                upsert_real_trade(trade)
            except Exception as exc:
                log.warning("DB sync update failed for ticket=%d: %s", ticket, exc)
        else:
            # Position gone — closed by MT5 (SL/TP or manual)
            closed_tickets.append(ticket)
            trade = {
                "ticket":    ticket,
                "strategy":  meta["strategy"],
                "symbol":    meta["symbol"],
                "direction": meta["direction"],
                "lots":      meta["lots"],
                "open_price": meta["fill_price"],
                "sl_price":  meta.get("sl_price"),
                "tp_price":  meta.get("tp_price"),
                "opened_at": meta["opened_at"],
                "closed_at": datetime.now(tz=timezone.utc),
                "is_open":   False,
            }
            try:
                upsert_real_trade(trade)
                log.info("Position closed by MT5: ticket=%d strategy=%s", ticket, meta["strategy"])
            except Exception as exc:
                log.warning("DB close update failed for ticket=%d: %s", ticket, exc)

    if closed_tickets:
        with _tracker_lock:
            for t in closed_tickets:
                _tracked_tickets.pop(t, None)


# ── Startup: rebuild tracker from DB ─────────────────────────────────────────

def _load_open_trades_from_db() -> None:
    """On startup, rebuild _tracked_tickets from open real_trades in Postgres.

    Protects against loss of in-memory state across restarts.
    """
    if not POSTGRES_URL:
        log.warning("POSTGRES_URL not set — cannot reload open trades on startup")
        return
    try:
        conn = psycopg2.connect(POSTGRES_URL)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM real_trades WHERE is_open = TRUE",
            )
            rows = cur.fetchall()
        conn.close()
    except Exception as exc:
        log.error("Could not load open trades from DB: %s", exc)
        return

    with _tracker_lock:
        for row in rows:
            ticket = row["ticket"]
            opened_at = row["opened_at"]
            if isinstance(opened_at, str):
                opened_at = datetime.fromisoformat(opened_at)
            _tracked_tickets[ticket] = {
                "strategy":   row["strategy"],
                "symbol":     row["symbol"],
                "direction":  row["direction"],
                "lots":       row["lots"],
                "fill_price": row["open_price"],
                "sl_price":   row.get("sl_price"),
                "tp_price":   row.get("tp_price"),
                "opened_at":  opened_at,
            }

    log.info("Loaded %d open trade(s) from DB into tracker", len(rows))


# ── Heartbeat (background thread) ─────────────────────────────────────────────

def _heartbeat_thread(r: redis_lib.Redis) -> None:
    while True:
        try:
            r.publish(
                HEARTBEAT_CHANNEL,
                json.dumps({"service": "executor", "ts": datetime.now(tz=timezone.utc).isoformat()}),
            )
        except Exception as exc:
            log.warning("Heartbeat publish failed: %s", exc)
        time.sleep(HEARTBEAT_INTERVAL)


# ── Main loop ─────────────────────────────────────────────────────────────────

def main() -> None:
    log.info(
        "executor starting | bridge=%s | redis=%s | tracking %s %s",
        BRIDGE_URL, REDIS_URL, SYMBOL, TIMEFRAME,
    )

    # Reload open positions from DB (crash recovery)
    _load_open_trades_from_db()

    r = redis_lib.from_url(REDIS_URL, decode_responses=True)

    # Start heartbeat daemon
    hb = threading.Thread(
        target=_heartbeat_thread, args=(r,), daemon=True, name="heartbeat"
    )
    hb.start()

    # Subscribe to both signal and candle channels
    pubsub = r.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(SIGNAL_ALL, CANDLE_SUB_CHANNEL)
    log.info("Subscribed to: %s, %s", SIGNAL_ALL, CANDLE_SUB_CHANNEL)

    for message in pubsub.listen():
        channel = message.get("channel", "")
        data = message.get("data", "")

        if not isinstance(data, str):
            continue

        try:
            if channel == SIGNAL_ALL:
                handle_signal(data)

            elif channel == CANDLE_SUB_CHANNEL:
                # Sync positions on each new candle (every ~5 min under normal market hours)
                sync_positions()

        except Exception as exc:
            log.error("Unhandled error processing message on %s: %s", channel, exc)


if __name__ == "__main__":
    main()
