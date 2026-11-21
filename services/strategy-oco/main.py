"""OCO Dual-Stop strategy service.

Places a BUY STOP above and SELL STOP below the current price. Whichever
triggers first becomes the active position; the opposite is cancelled.
Position closes when the configured profit target or max-loss limit is hit.

This service is backtest-only. No live Redis loop is wired. It registers
with the dashboard on startup so the sidebar and Backtest tab pick it up
automatically.

POST /backtest  → { trades: [...] }
GET  /health    → { ok: true, strategy: "oco-dual-stop" }

Candle format expected (sent by dashboard /api/backtest/run):
  { symbol, timeframe, timestamp, open, high, low, close, volume, spread }

Backtest params (all optional — defaults shown):
  distance_pips       50      pips from ref to each stop
  profit_target_pips  50      pips profit to close position
  max_loss_pips       150     pips loss to cut position
  lots                0.01    lot size per cycle
  spread_pips         21      broker spread (subtracted from BUY entry)
  commission_per_lot  0.0     USD commission per lot per side
  tick_value          1.0     USD per point per full lot (XAUUSD default)
  point               0.01    minimum price movement (XAUUSD default)
  max_holding_bars    2880    max M1 bars to hold (48 h = 2 days)
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import requests
import uvicorn
from fastapi import FastAPI
from fastapi import Body
from pydantic import BaseModel

# shared/ is copied to /app/shared/ by the Dockerfile
sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared.backtest_http import candles_from_dicts
from shared.models import Candle

log = logging.getLogger(__name__)

STRATEGY_NAME = os.getenv("STRATEGY_NAME", "oco-dual-stop")
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "").rstrip("/")
HTTP_PORT     = int(os.getenv("HTTP_PORT", "8080"))


# ─────────────────────────────────────────────────────────────────────────────
# OCO backtest engine
# ─────────────────────────────────────────────────────────────────────────────

def _run_oco_backtest(candles: list[Candle], params: dict) -> list[dict]:
    """Simulate the OCO dual-stop strategy on M1 candles.

    Returns one trade dict per completed cycle (the triggered side only).

    Conservative intrabar rules
    ---------------------------
    Both stops crossed same bar  →  SELL assumed to have triggered first
                                    (price dipped to sell_stop, then reversed).
    Both TP and SL crossed same monitoring bar  →  SL wins.

    State machine
    -------------
    IDLE             → place buy_stop / sell_stop, → WAITING_TRIGGER
    WAITING_TRIGGER  → check bar.high / bar.low, → MONITORING
    MONITORING       → track floating P&L, close at TP / SL / TIMEOUT
    """
    point            = float(params.get("point",             0.01))
    distance_pips    = float(params.get("distance_pips",     50))
    profit_tgt_pips  = float(params.get("profit_target_pips", 50))
    max_loss_pips    = float(params.get("max_loss_pips",      150))
    lots             = float(params.get("lots",               0.01))
    spread_pips      = float(params.get("spread_pips",        21))
    commission_lot   = float(params.get("commission_per_lot", 0.0))
    tick_value       = float(params.get("tick_value",         1.0))
    max_holding_bars = int(  params.get("max_holding_bars",   2880))

    # Distances in price units
    dist      = distance_pips   * point
    tgt       = profit_tgt_pips * point
    max_loss  = max_loss_pips   * point
    spread    = spread_pips     * point

    # USD P&L multiplier: (price_diff / point) * tick_value * lots
    multiplier = tick_value / point

    # State
    state         = "IDLE"
    buy_stop      = 0.0
    sell_stop     = 0.0
    direction     = ""
    entry_price   = 0.0
    entry_bar_idx = 0

    trades: list[dict] = []

    i = 0
    while i < len(candles):
        bar = candles[i]

        # ── IDLE: establish new pending orders ────────────────────────────────
        if state == "IDLE":
            ref       = bar.close
            # BUY STOP is placed on the ask side (above mid + spread)
            buy_stop  = round(ref + dist + spread, 10)
            # SELL STOP is placed on the bid side (below mid)
            sell_stop = round(ref - dist,          10)
            state = "WAITING_TRIGGER"
            i += 1
            continue

        # ── WAITING_TRIGGER: detect which stop activated ──────────────────────
        if state == "WAITING_TRIGGER":
            buy_trig  = bar.high >= buy_stop
            sell_trig = bar.low  <= sell_stop

            if buy_trig and sell_trig:
                # Race condition: both levels breached same M1 bar.
                # Assume SELL triggered first (price moved down, then reversed).
                direction, entry_price = "SELL", sell_stop
                entry_bar_idx = i
                state = "MONITORING"
            elif buy_trig:
                direction, entry_price = "BUY",  buy_stop
                entry_bar_idx = i
                state = "MONITORING"
            elif sell_trig:
                direction, entry_price = "SELL", sell_stop
                entry_bar_idx = i
                state = "MONITORING"

            i += 1
            continue

        # ── MONITORING: check exit each bar ──────────────────────────────────
        if state == "MONITORING":
            bars_held = i - entry_bar_idx

            if direction == "BUY":
                tp_price = entry_price + tgt
                sl_price = entry_price - max_loss
                hit_tp   = bar.high >= tp_price
                hit_sl   = bar.low  <= sl_price
            else:
                tp_price = entry_price - tgt
                sl_price = entry_price + max_loss
                hit_tp   = bar.low  <= tp_price
                hit_sl   = bar.high >= sl_price

            timeout = bars_held >= max_holding_bars

            close_price: float | None = None
            outcome:     str   | None = None

            if hit_sl and hit_tp:        # both — conservative: SL first
                close_price, outcome = sl_price, "SL"
            elif hit_sl:
                close_price, outcome = sl_price, "SL"
            elif hit_tp:
                close_price, outcome = tp_price, "TP"
            elif timeout:
                close_price, outcome = bar.close, "TIMEOUT"

            if outcome is None:
                i += 1
                continue

            # ── P&L ──────────────────────────────────────────────────────────
            sign        = 1.0 if direction == "BUY" else -1.0
            gross       = round((close_price - entry_price) * sign * lots * multiplier, 2)
            spread_cost = round(spread * lots * multiplier, 2)       # paid once at entry
            commission  = round(commission_lot * lots * 2,   2)      # round-trip
            net         = round(gross - spread_cost - commission,     2)

            trades.append({
                "direction":   direction,
                "entry_price": round(entry_price, 2),
                "sl_price":    round(sl_price,    2),
                "tp_price":    round(tp_price,    2),
                "lots":        lots,
                "close_price": round(close_price, 2),
                "outcome":     outcome,
                "gross_pnl":   gross,
                "commission":  round(spread_cost + commission, 2),
                "net_pnl":     net,
                "opened_at":   candles[entry_bar_idx].timestamp.isoformat(),
                "closed_at":   bar.timestamp.isoformat(),
                # OCO-specific metadata — ignored by generic trade table,
                # available for future OCO-specific chart overlays
                "meta": {
                    "buy_stop":   round(buy_stop,   2),
                    "sell_stop":  round(sell_stop,  2),
                    "bars_held":  bars_held,
                },
            })

            state = "IDLE"
            i += 1
            continue

        i += 1  # safety fallthrough (should never reach here)

    # Force-close any position still open at end of data
    if state == "MONITORING" and candles:
        last      = candles[-1]
        cp        = last.close
        sign      = 1.0 if direction == "BUY" else -1.0
        if direction == "BUY":
            tp_price = entry_price + tgt
            sl_price = entry_price - max_loss
        else:
            tp_price = entry_price - tgt
            sl_price = entry_price + max_loss
        gross       = round((cp - entry_price) * sign * lots * multiplier, 2)
        spread_cost = round(spread * lots * multiplier, 2)
        commission  = round(commission_lot * lots * 2,   2)
        net         = round(gross - spread_cost - commission, 2)
        trades.append({
            "direction":   direction,
            "entry_price": round(entry_price, 2),
            "sl_price":    round(sl_price,    2),
            "tp_price":    round(tp_price,    2),
            "lots":        lots,
            "close_price": round(cp, 2),
            "outcome":     "FORCE_CLOSE",
            "gross_pnl":   gross,
            "commission":  round(spread_cost + commission, 2),
            "net_pnl":     net,
            "opened_at":   candles[entry_bar_idx].timestamp.isoformat(),
            "closed_at":   last.timestamp.isoformat(),
            "meta": {
                "buy_stop":  round(buy_stop,  2),
                "sell_stop": round(sell_stop, 2),
                "bars_held": len(candles) - 1 - entry_bar_idx,
            },
        })

    return trades


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard registration
# ─────────────────────────────────────────────────────────────────────────────

def _register_with_dashboard() -> None:
    if not DASHBOARD_URL:
        return
    try:
        requests.post(
            f"{DASHBOARD_URL}/api/strategies/register",
            json={
                "name":         STRATEGY_NAME,
                "display_name": "OCO Dual Stop",
                "description":  "BUY STOP + SELL STOP breakout — OCO cancellation on trigger",
                "mode":         "shadow",
            },
            timeout=5,
        )
        log.info("Registered with dashboard: %s", STRATEGY_NAME)
    except Exception as exc:
        log.warning("Could not register with dashboard (non-fatal): %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI app
# ─────────────────────────────────────────────────────────────────────────────

class BacktestRequest(BaseModel):
    candles: list[dict]
    params:  dict = {}


class BacktestResponse(BaseModel):
    trades: list[dict]


@asynccontextmanager
async def lifespan(app: FastAPI):
    threading.Thread(target=_register_with_dashboard, daemon=True).start()
    yield


app = FastAPI(title="OCO Dual Stop", version="1.0.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "strategy": STRATEGY_NAME}


@app.post("/backtest", response_model=BacktestResponse)
async def backtest(body: BacktestRequest = Body(...)) -> Any:
    candles = candles_from_dicts(body.candles)
    if not candles:
        return BacktestResponse(trades=[])
    try:
        trades = _run_oco_backtest(candles, body.params or {})
    except Exception as exc:
        log.exception("OCO backtest failed: %s", exc)
        return BacktestResponse(trades=[])
    return BacktestResponse(trades=trades)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    uvicorn.run(app, host="0.0.0.0", port=HTTP_PORT, log_level="warning")
