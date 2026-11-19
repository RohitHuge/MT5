"""FastAPI backtest server.

Run from the backtesting/ directory:
    python server/app.py
Then open http://localhost:8000
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE     = Path(__file__).parent.resolve()
_ROOT     = _HERE.parent                      # backtesting/
_SRC      = _ROOT / "src"
_CACHE    = _ROOT / "data" / "processed"
_STATIC   = _HERE / "static"

sys.path.insert(0, str(_SRC))

from mt5_liquidity.config import AccountRiskConfig, BacktestConfig, load_config
from mt5_liquidity.data.cache import CandleCache
from mt5_liquidity.data.mt5_adapter import MT5Adapter
from mt5_liquidity.data.history import fetch_m5
from mt5_liquidity.backtest.engine import SymbolMeta
from mt5_liquidity.backtest.scalp_engine import run_scalp_backtest

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="Scalp Backtest Server", version="1.0")
app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

# Fixed XAUUSD contract spec for Vantage Markets
_XAUUSD = SymbolMeta(
    point=0.01, tick_value=1.0, tick_size=0.01,
    volume_min=0.01, volume_max=500.0, volume_step=0.01,
)

# Preset weeks shown as quick-pick buttons in the UI
PRESETS = [
    {"label": "Nov 3–7",   "from": "2026-11-03", "to": "2026-11-08"},
    {"label": "Nov 9–13",  "from": "2026-11-09", "to": "2026-11-14"},
    {"label": "Nov 10–14", "from": "2026-11-10", "to": "2026-11-15"},
]


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------
class RunRequest(BaseModel):
    from_date:        str   = Field(...,    description="Start date YYYY-MM-DD (inclusive)")
    to_date:          str   = Field(...,    description="End date YYYY-MM-DD (exclusive)")
    daily_target:     float = Field(100.0,  ge=0,     description="Stop new entries after +$N profit/day")
    daily_loss:       float = Field(200.0,  ge=0,     description="Stop new entries after -$N loss/day")
    risk_pct:         float = Field(0.5,    ge=0.01, le=10.0)
    rr_target:        float = Field(1.5,    ge=0.5,  le=5.0)
    rsi_min_buy:      float = Field(55.0,   ge=30.0, le=80.0)
    rsi_max_sell:     float = Field(45.0,   ge=20.0, le=70.0)
    spread_points:    int   = Field(21,     ge=0,    le=200)
    max_positions:    int   = Field(2,      ge=1,    le=10)
    equity:           float = Field(11285.0, ge=100.0)
    sl_swing_bars:    int   = Field(4,      ge=1,    le=20)
    sl_buffer_points: int   = Field(15,     ge=0,    le=100)
    no_guardrails:    bool  = Field(False,  description="Ignore daily target & loss limits entirely")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _load_m5(from_dt: datetime, to_dt: datetime):
    cache = CandleCache(str(_CACHE))
    m5 = cache.load("XAUUSD", "M5", from_dt, to_dt)
    if m5 is not None:
        logger.info("Cache hit — %d candles", len(m5))
        return m5

    logger.info("Cache miss for %s→%s — connecting MT5", from_dt.date(), to_dt.date())
    cfg = load_config()
    adapter = MT5Adapter(cfg)
    if not adapter.connect():
        raise HTTPException(
            status_code=503,
            detail="No cached data for this date range and MT5 is unavailable. "
                   "Open MT5 terminal and retry, or choose a cached week.",
        )
    try:
        m5 = fetch_m5(adapter, "XAUUSD", from_dt, to_dt)
        cache.save(m5, "XAUUSD", "M5", from_dt, to_dt)
        logger.info("Fetched & cached %d candles from MT5", len(m5))
        return m5
    finally:
        adapter.disconnect()


def _days_breakdown(trades) -> list[dict]:
    """Group trades by UTC calendar date."""
    from collections import defaultdict
    buckets: dict[str, list] = defaultdict(list)
    for t in trades:
        d = t.fill_ts.astimezone(timezone.utc).date().isoformat()
        buckets[d].append(t)
    out = []
    for date_str in sorted(buckets):
        ts = buckets[date_str]
        pnl = sum(t.net_pnl_usd for t in ts)
        wins = sum(1 for t in ts if t.net_pnl_usd > 0)
        out.append({
            "date":    date_str,
            "trades":  len(ts),
            "winners": wins,
            "losers":  len(ts) - wins,
            "pnl":     round(pnl, 2),
        })
    return out


def _serialize_result(result, equity_start: float) -> dict:
    s = result.stats

    trades_out = [
        {
            "id":        t.setup_id,
            "direction": t.direction,
            "fill":      round(t.fill_price, 2),
            "close":     round(t.close_price, 2),
            "tp":        round(t.tp_price, 2),
            "sl":        round(t.sl_price, 2),
            "lots":      t.lots,
            "pnl":       t.net_pnl_usd,
            "exit":      t.exit_reason,
            "fill_ts":   t.fill_ts.isoformat(),
            "close_ts":  t.close_ts.isoformat(),
            "bars_held": t.bars_held,
        }
        for t in result.closed_positions
    ]

    eq_out = [
        {"ts": ts.isoformat(), "eq": round(eq, 2)}
        for ts, eq in result.equity_curve
    ]

    risk_events_out = []
    for ev in result.risk_events:
        risk_events_out.append({
            "ts":        ev.timestamp.isoformat(),
            "type":      ev.event_type,
            "state":     ev.state,
            "reason":    ev.reason,
            "observed":  ev.observed_value,
            "threshold": ev.threshold_value,
            "daily_pnl": ev.daily_pnl,
            "action":    ev.action,
        })

    return {
        "stats": {
            "total_trades":           s.total_trades,
            "winners":                s.winners,
            "losers":                 s.losers,
            "win_rate":               round(s.win_rate * 100, 1),
            "net_pnl":                s.net_pnl,
            "gross_profit":           s.gross_profit,
            "gross_loss":             s.gross_loss,
            "profit_factor":          s.profit_factor if s.profit_factor != float("inf") else 999,
            "avg_win":                s.avg_win,
            "avg_loss":               s.avg_loss,
            "max_drawdown_usd":       s.max_drawdown_usd,
            "max_drawdown_pct":       round(s.max_drawdown_pct, 2),
            "final_equity":           s.final_equity,
            "initial_equity":         equity_start,
            "max_consecutive_wins":   s.max_consecutive_wins,
            "max_consecutive_losses": s.max_consecutive_losses,
        },
        "trades":               trades_out,
        "equity_curve":         eq_out,
        "days":                 _days_breakdown(result.closed_positions),
        "risk_events":          risk_events_out,
        "risk_blocked_by_reason": result.risk_blocked_by_reason,
        "skips": {
            "max_pos": result.setups_skipped_max_pos,
            "session": result.setups_skipped_session,
            "risk":    result.setups_skipped_risk,
            "sizing":  result.setups_skipped_sizing,
        },
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(str(_STATIC / "index.html"))


@app.get("/api/presets")
def get_presets():
    """Return preset weeks with cache status."""
    cache = CandleCache(str(_CACHE))
    out = []
    for p in PRESETS:
        try:
            cached = cache.load("XAUUSD", "M5", _dt(p["from"]), _dt(p["to"])) is not None
        except Exception:
            cached = False
        out.append({**p, "cached": cached})
    return out


@app.post("/api/backtest")
def backtest(req: RunRequest):
    try:
        from_dt = _dt(req.from_date)
        to_dt   = _dt(req.to_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Bad date format: {exc}")

    if from_dt >= to_dt:
        raise HTTPException(status_code=400, detail="from_date must be before to_date")

    m5 = _load_m5(from_dt, to_dt)

    daily_target = 999_999.0 if req.no_guardrails else req.daily_target
    daily_loss   = 999_999.0 if req.no_guardrails else req.daily_loss

    cfg = BacktestConfig(
        initial_equity    = req.equity,
        risk_pct          = req.risk_pct,
        rr_target         = req.rr_target,
        max_positions     = req.max_positions,
        spread_points     = req.spread_points,
        skip_sessions     = ["TOKYO", "OFF_SESSION"],
        account_risk      = AccountRiskConfig(
            enabled          = True,
            daily_target_usd = daily_target,
            daily_loss_usd   = daily_loss,
        ),
    )

    result = run_scalp_backtest(
        m5_candles       = m5,
        config           = cfg,
        symbol_meta      = _XAUUSD,
        sl_swing_bars    = req.sl_swing_bars,
        sl_buffer_points = req.sl_buffer_points,
        rsi_min_buy      = req.rsi_min_buy,
        rsi_max_sell     = req.rsi_max_sell,
    )

    return _serialize_result(result, req.equity)


if __name__ == "__main__":
    import os
    os.chdir(str(_ROOT))
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
