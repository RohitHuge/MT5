"""Shadow Tracker — records virtual trades for ALL strategies from signals:all.

Responsibilities
  1. Subscribe to Redis ``signals:all`` — persist every incoming signal from every
     strategy as a row in ``virtual_trades`` and keep an in-memory copy.
     This includes the live strategy's signals, which allows comparing real trade
     performance against what the shadow model tracked for the same signals.
  2. Subscribe to ``candles:XAUUSD:M5`` — on every new candle check all open
     virtual trades for SL/TP hits and close them in both DB and memory.
  3. At UTC midnight — aggregate the previous day's closed virtual trades into
     ``daily_summary`` (one row per strategy).
  4. Publish a heartbeat every 10 seconds so the dashboard can detect outages.

On startup the service rebuilds its in-memory open-trade dict from the DB so
that a container restart does not lose track of positions that were already open.

Environment variables (all required unless noted):
  REDIS_URL          redis://redis:6379
  POSTGRES_URL       postgresql://trader:...@postgres:5432/mt5
  MT5_BRIDGE_URL     http://host.docker.internal:8000
  SYMBOL             XAUUSD  (default)
  TIMEFRAME          M5      (default)
  COMMISSION_PER_LOT USD per standard lot per side, default 3.5
  LOG_LEVEL          INFO    (default)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import psycopg2
import psycopg2.extras
import redis
import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap — env, logging, shared imports
# ---------------------------------------------------------------------------

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("shadow-tracker")

# shared/ is on PYTHONPATH=/app (set in Dockerfile)
from shared.channels import CANDLE_CHANNEL, HEARTBEAT, SIGNAL_ALL  # noqa: E402
from shared.models import Candle, Signal  # noqa: E402
import shared.db as db  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REDIS_URL          = os.environ["REDIS_URL"]
SYMBOL             = os.getenv("SYMBOL", "XAUUSD")
TIMEFRAME          = os.getenv("TIMEFRAME", "M5")
MT5_BRIDGE_URL     = os.environ["MT5_BRIDGE_URL"].rstrip("/")
COMMISSION_PER_LOT = float(os.getenv("COMMISSION_PER_LOT", "3.5"))  # per std lot per side

CANDLE_CH  = CANDLE_CHANNEL.format(symbol=SYMBOL, timeframe=TIMEFRAME)
HEARTBEAT_CH = HEARTBEAT.format(service="shadow-tracker")

# ---------------------------------------------------------------------------
# Symbol metadata — fetched once from mt5-bridge on startup
# ---------------------------------------------------------------------------

_tick_value: float = 1.0   # USD per point per standard lot
_point: float = 0.01        # smallest price increment


def _fetch_symbol_info(retries: int = 10, delay: float = 5.0) -> None:
    """Populate _tick_value and _point from the bridge. Retries until ready."""
    global _tick_value, _point
    url = f"{MT5_BRIDGE_URL}/symbol/{SYMBOL}"
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            _tick_value = float(data["tick_value"])
            _point      = float(data["point"])
            log.info("Symbol %s: tick_value=%.4f  point=%.4f", SYMBOL, _tick_value, _point)
            return
        except Exception as exc:
            log.warning("Symbol fetch attempt %d/%d failed: %s", attempt, retries, exc)
            if attempt < retries:
                time.sleep(delay)
    log.error(
        "Could not fetch symbol info after %d attempts — using defaults "
        "(tick_value=%.4f, point=%.4f). PnL figures may be incorrect.",
        retries, _tick_value, _point,
    )


# ---------------------------------------------------------------------------
# In-memory open trade state
# Key: DB row id (int)
# Value: dict mirroring the virtual_trades columns we need for SL/TP checks
# ---------------------------------------------------------------------------

_open_trades: dict[int, dict[str, Any]] = {}
_trades_lock = threading.Lock()


def _rebuild_from_db() -> None:
    """On startup, reload any trades that were OPEN before the last restart."""
    rows = db.get_open_virtual_trades()
    with _trades_lock:
        for row in rows:
            _open_trades[row["id"]] = {
                "id":          row["id"],
                "strategy":    row["strategy"],
                "symbol":      row["symbol"],
                "direction":   row["direction"],
                "entry_price": float(row["entry_price"]),
                "sl_price":    float(row["sl_price"]),
                "tp_price":    float(row["tp_price"]),
                "lots":        float(row["lots"]),
                "signal_ts":   row["signal_ts"],
            }
    log.info("Rebuilt %d open virtual trade(s) from DB.", len(rows))


# ---------------------------------------------------------------------------
# PnL helpers
# ---------------------------------------------------------------------------

def _calc_pnl(
    direction: str,
    entry_price: float,
    close_price: float,
    lots: float,
) -> tuple[float, float, float]:
    """Return (gross_pnl, commission, net_pnl) for a completed virtual trade."""
    sign = 1.0 if direction == "BUY" else -1.0
    gross = round((close_price - entry_price) * sign * lots * _tick_value / _point, 2)
    comm  = round(lots * COMMISSION_PER_LOT, 2)   # per side (entry + exit = 2×)
    net   = round(gross - comm, 2)
    return gross, comm, net


# ---------------------------------------------------------------------------
# Signal handler
# ---------------------------------------------------------------------------

def _on_signal(channel: str, data: str) -> None:
    """Persist an incoming signal from any strategy as a new OPEN virtual trade.

    All strategies publish to signals:all — shadow-tracker records them all.
    This includes the live strategy's signals, enabling real vs virtual comparison.
    """
    try:
        sig = Signal.from_json(data)
    except Exception as exc:
        log.warning("Could not parse signal from %s: %s — raw: %.120s", channel, exc, data)
        return

    try:
        trade_id = db.insert_virtual_trade(
            strategy    = sig.strategy,
            symbol      = sig.symbol,
            direction   = sig.direction,
            entry_price = sig.entry_price,
            sl_price    = sig.sl_price,
            tp_price    = sig.tp_price,
            lots        = sig.lots,
            signal_ts   = sig.candle_ts,
        )
    except Exception as exc:
        log.error("DB insert failed for signal %s: %s", sig, exc)
        return

    trade = {
        "id":          trade_id,
        "strategy":    sig.strategy,
        "symbol":      sig.symbol,
        "direction":   sig.direction,
        "entry_price": sig.entry_price,
        "sl_price":    sig.sl_price,
        "tp_price":    sig.tp_price,
        "lots":        sig.lots,
        "signal_ts":   sig.candle_ts,
    }
    with _trades_lock:
        _open_trades[trade_id] = trade

    log.info(
        "NEW virtual trade #%d  %s %s %s  entry=%.2f  sl=%.2f  tp=%.2f  lots=%.2f",
        trade_id, sig.strategy, sig.direction, sig.symbol,
        sig.entry_price, sig.sl_price, sig.tp_price, sig.lots,
    )


# ---------------------------------------------------------------------------
# Candle handler — SL/TP checking
# ---------------------------------------------------------------------------

def _check_hit(trade: dict, candle: Candle) -> tuple[bool, str, float] | tuple[bool, None, None]:
    """Return (hit, outcome, close_price) or (False, None, None).

    SL takes precedence over TP when both are reached in the same candle —
    conservative convention matching the backtester.
    """
    d = trade["direction"]
    if d == "BUY":
        if candle.low <= trade["sl_price"]:
            return True, "SL", trade["sl_price"]
        if candle.high >= trade["tp_price"]:
            return True, "TP", trade["tp_price"]
    else:  # SELL
        if candle.high >= trade["sl_price"]:
            return True, "SL", trade["sl_price"]
        if candle.low <= trade["tp_price"]:
            return True, "TP", trade["tp_price"]
    return False, None, None


def _on_candle(data: str) -> None:
    """Check every open virtual trade for SL/TP hits on this candle."""
    try:
        candle = Candle.from_json(data)
    except Exception as exc:
        log.warning("Could not parse candle: %s — raw: %.120s", exc, data)
        return

    # Snapshot the current open trades to avoid holding the lock during DB writes
    with _trades_lock:
        snapshot = list(_open_trades.values())

    to_close: list[tuple[dict, str, float]] = []
    for trade in snapshot:
        hit, outcome, close_price = _check_hit(trade, candle)
        if hit:
            to_close.append((trade, outcome, close_price))

    for trade, outcome, close_price in to_close:
        gross, comm, net = _calc_pnl(
            trade["direction"], trade["entry_price"], close_price, trade["lots"]
        )
        try:
            db.close_virtual_trade(
                trade_id    = trade["id"],
                close_price = close_price,
                outcome     = outcome,
                gross_pnl   = gross,
                commission  = comm,
                net_pnl     = net,
            )
        except Exception as exc:
            log.error("DB close failed for trade #%d: %s", trade["id"], exc)
            continue

        with _trades_lock:
            _open_trades.pop(trade["id"], None)

        log.info(
            "CLOSED virtual trade #%d  %s %s %s  outcome=%s  close=%.2f  net_pnl=%.2f",
            trade["id"], trade["strategy"], trade["direction"], trade["symbol"],
            outcome, close_price, net,
        )


# ---------------------------------------------------------------------------
# Daily summary aggregation  (runs once per day at UTC midnight)
# ---------------------------------------------------------------------------

def _aggregate_yesterday() -> None:
    """Read yesterday's closed virtual trades from DB and write daily_summary rows."""
    yesterday = (datetime.now(tz=timezone.utc) - timedelta(days=1)).date()
    log.info("Running daily summary aggregation for %s …", yesterday)

    sql = """
        SELECT
            strategy,
            COUNT(*)                                       AS trades,
            COUNT(CASE WHEN net_pnl > 0 THEN 1 END)       AS winners,
            COUNT(CASE WHEN net_pnl <= 0 THEN 1 END)      AS losers,
            COALESCE(SUM(gross_pnl), 0.0)                 AS gross_pnl,
            COALESCE(SUM(commission), 0.0)                AS commission,
            COALESCE(SUM(net_pnl), 0.0)                   AS net_pnl
        FROM virtual_trades
        WHERE outcome != 'OPEN'
          AND DATE(closed_at AT TIME ZONE 'UTC') = %s
        GROUP BY strategy
    """
    try:
        conn = psycopg2.connect(os.environ["POSTGRES_URL"])
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (yesterday,))
                rows = cur.fetchall()
        finally:
            conn.close()
    except Exception as exc:
        log.error("Daily summary DB query failed: %s", exc)
        return

    for row in rows:
        trades  = row["trades"]
        winners = row["winners"]
        win_rate = winners / trades if trades > 0 else 0.0
        try:
            db.upsert_daily_summary(
                strategy   = row["strategy"],
                date       = yesterday,
                is_shadow  = True,
                trades     = trades,
                winners    = winners,
                losers     = row["losers"],
                gross_pnl  = float(row["gross_pnl"]),
                commission = float(row["commission"]),
                net_pnl    = float(row["net_pnl"]),
                win_rate   = round(win_rate, 4),
            )
            log.info(
                "Daily summary written: %s  date=%s  trades=%d  net_pnl=%.2f",
                row["strategy"], yesterday, trades, row["net_pnl"],
            )
        except Exception as exc:
            log.error("upsert_daily_summary failed for %s: %s", row["strategy"], exc)


def _schedule_midnight_aggregation() -> None:
    """Schedule _aggregate_yesterday() to fire at next UTC midnight, then reschedule."""
    now      = datetime.now(tz=timezone.utc)
    midnight = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=5, microsecond=0
    )
    delay = (midnight - now).total_seconds()

    def _fire():
        _aggregate_yesterday()
        _schedule_midnight_aggregation()   # reschedule for the following midnight

    t = threading.Timer(delay, _fire)
    t.daemon = True
    t.start()
    log.info("Daily summary scheduled in %.0f seconds (at %s UTC).", delay, midnight.isoformat())


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

def _start_heartbeat(r: redis.Redis) -> None:
    """Publish a heartbeat JSON payload every 10 seconds in a daemon thread."""
    def _loop():
        while True:
            try:
                payload = json.dumps({
                    "service":    "shadow-tracker",
                    "ts":         datetime.now(tz=timezone.utc).isoformat(),
                    "open_trades": len(_open_trades),
                })
                r.publish(HEARTBEAT_CH, payload)
            except Exception as exc:
                log.warning("Heartbeat publish failed: %s", exc)
            time.sleep(10)

    t = threading.Thread(target=_loop, daemon=True, name="heartbeat")
    t.start()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    log.info("Shadow Tracker starting …")

    # 1. Fetch symbol metadata (needed for PnL calc)
    _fetch_symbol_info()

    # 2. Rebuild in-memory state from DB (handles restarts)
    _rebuild_from_db()

    # 3. Connect to Redis
    r = redis.from_url(REDIS_URL, decode_responses=True)
    r.ping()
    log.info("Redis connected: %s", REDIS_URL)

    # 4. Start background tasks
    _start_heartbeat(r)
    _schedule_midnight_aggregation()

    # 5. Subscribe
    ps = r.pubsub(ignore_subscribe_messages=True)
    ps.subscribe(SIGNAL_ALL)   # all strategy signals, every strategy
    ps.subscribe(CANDLE_CH)    # candles:XAUUSD:M5
    log.info(
        "Subscribed to '%s' and '%s'",
        SIGNAL_ALL, CANDLE_CH,
    )

    # 6. Message loop
    log.info("Shadow Tracker ready — listening.")
    for msg in ps.listen():
        if msg is None:
            continue

        msg_type = msg.get("type")
        channel  = msg.get("channel", "")
        data     = msg.get("data", "")

        if msg_type == "message" and channel == SIGNAL_ALL:
            # Incoming signal from any strategy container — record as virtual trade
            _on_signal(channel, data)

        elif msg_type == "message" and channel == CANDLE_CH:
            # New closed candle — check SL/TP on all open virtual trades
            _on_candle(data)


if __name__ == "__main__":
    main()
