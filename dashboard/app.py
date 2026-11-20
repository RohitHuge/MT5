"""Dashboard service — FastAPI backend for the MT5 multi-strategy platform.

Endpoints:
  GET  /                                       renders index.html
  GET  /strategy/{name}                        renders strategy.html
  GET  /api/health                             DB + Redis + bridge connectivity
  GET  /api/strategies                         all strategies + today's P&L
  GET  /api/strategy/{name}/performance        daily rows (default last 30 days)
  GET  /api/strategy/{name}/equity_curve       cumulative equity series
  GET  /api/compare                            all strategies side-by-side
  GET  /api/live_positions                     open real + shadow positions
  POST /api/strategy/{name}/set_mode           promote / demote strategy

All DB calls go through shared.db which owns the connection pool.
Bridge calls are plain requests.get() with a short timeout — if the bridge
is unreachable the endpoint returns a degraded-but-valid response instead of
crashing, so the dashboard stays usable even when MT5 is offline.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone, timedelta
from typing import Any

import redis as redis_lib
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# shared/ lives one level up from dashboard/ inside the container (COPY shared/ /app/shared/)
import sys
sys.path.insert(0, "/app")

from shared import db

load_dotenv()

log = logging.getLogger("dashboard")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)

# ─────────────────────────────────────────────────────────────────────────────
# App + template setup
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="MT5 Dashboard", version="1.0.0")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

MT5_BRIDGE_URL = os.getenv("MT5_BRIDGE_URL", "http://host.docker.internal:8000")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")


def _bridge_get(path: str, timeout: float = 3.0) -> dict | list | None:
    """Call the MT5 bridge; return None on any error (bridge may be Windows-only)."""
    try:
        resp = requests.get(f"{MT5_BRIDGE_URL}{path}", timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.warning("Bridge call %s failed: %s", path, exc)
        return None


def _redis_client() -> redis_lib.Redis:
    return redis_lib.from_url(REDIS_URL, decode_responses=True)


# ─────────────────────────────────────────────────────────────────────────────
# HTML pages
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/strategy/{name}", response_class=HTMLResponse)
async def strategy_page(request: Request, name: str):
    return templates.TemplateResponse("strategy.html", {"request": request, "strategy_name": name})


# ─────────────────────────────────────────────────────────────────────────────
# Health check
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    result: dict[str, Any] = {
        "status": "ok",
        "db": False,
        "redis": False,
        "bridge": False,
        "checked_at": datetime.now(tz=timezone.utc).isoformat(),
    }

    # DB check
    try:
        db.get_all_strategies()
        result["db"] = True
    except Exception as exc:
        result["db_error"] = str(exc)
        result["status"] = "degraded"

    # Redis check
    try:
        r = _redis_client()
        r.ping()
        result["redis"] = True
    except Exception as exc:
        result["redis_error"] = str(exc)
        result["status"] = "degraded"

    # Bridge check
    bridge = _bridge_get("/health")
    if bridge:
        result["bridge"] = True
        result["bridge_detail"] = bridge
    else:
        result["status"] = "degraded"

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Strategies list
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/strategies")
async def list_strategies():
    """Return all strategies with current mode and today's P&L (real + shadow)."""
    strategies = db.get_all_strategies()

    today = date.today()
    enriched = []
    for s in strategies:
        name = s["name"]
        # Determine is_shadow based on current mode
        is_shadow = s["mode"] != "live"
        try:
            perf = db.get_strategy_performance(name, days=1)
            today_rows = [r for r in perf if r["date"] == today or str(r["date"]) == str(today)]
            if today_rows:
                row = today_rows[0]
                s["today_pnl"] = row.get("net_pnl", 0.0)
                s["today_trades"] = row.get("trades", 0)
                s["today_win_rate"] = row.get("win_rate", 0.0)
            else:
                s["today_pnl"] = 0.0
                s["today_trades"] = 0
                s["today_win_rate"] = 0.0
        except Exception as exc:
            log.warning("Could not fetch today stats for %s: %s", name, exc)
            s["today_pnl"] = 0.0
            s["today_trades"] = 0
            s["today_win_rate"] = 0.0
        enriched.append(s)

    return enriched


# ─────────────────────────────────────────────────────────────────────────────
# Strategy performance
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/strategy/{name}/performance")
async def strategy_performance(name: str, days: int = 30):
    """Daily summary rows for the last N days (both real and shadow rows)."""
    try:
        rows = db.get_strategy_performance(name, days=days)
    except Exception as exc:
        log.error("performance query failed for %s: %s", name, exc)
        raise HTTPException(status_code=500, detail=str(exc))

    # Convert date objects to ISO strings for JSON serialisation
    for r in rows:
        if hasattr(r.get("date"), "isoformat"):
            r["date"] = r["date"].isoformat()

    return rows


@app.get("/api/strategy/{name}/equity_curve")
async def equity_curve(name: str, days: int = 30):
    """Cumulative equity series for a strategy (shadow rows preferred for shadow/off strategies).

    For live strategies, returns real trade rows (is_shadow=False) when available,
    falls back to shadow rows.  For shadow/off strategies always returns shadow rows.
    Empty list when no data yet — chart renders blank cleanly.
    """
    try:
        strategies = db.get_all_strategies()
        strat = next((s for s in strategies if s["name"] == name), None)
        if strat is None:
            raise HTTPException(status_code=404, detail=f"Strategy {name!r} not found")

        is_shadow = strat["mode"] != "live"
        rows = db.get_equity_curve(name, days=days, is_shadow=is_shadow)

        # If live strategy has no real rows yet, fall back to shadow curve
        if not rows and not is_shadow:
            rows = db.get_equity_curve(name, days=days, is_shadow=True)

    except HTTPException:
        raise
    except Exception as exc:
        log.error("equity_curve query failed for %s: %s", name, exc)
        raise HTTPException(status_code=500, detail=str(exc))

    for r in rows:
        if hasattr(r.get("date"), "isoformat"):
            r["date"] = r["date"].isoformat()

    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Comparison
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/compare")
async def compare(days: int = 30):
    """All strategies aggregated over the last N days (for comparison table).

    Also computes week and month sub-totals so the table can show
    'this week' and 'this month' columns without extra queries.
    """
    try:
        base = db.get_comparison_summary(days=days)
        week = db.get_comparison_summary(days=7)
        month = db.get_comparison_summary(days=30)
    except Exception as exc:
        log.error("compare query failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    week_map = {r["strategy"]: r for r in week}
    month_map = {r["strategy"]: r for r in month}

    result = []
    for row in base:
        name = row["strategy"]
        wk = week_map.get(name, {})
        mo = month_map.get(name, {})
        row["week_net_pnl"] = float(wk.get("total_net_pnl") or 0)
        row["month_net_pnl"] = float(mo.get("total_net_pnl") or 0)
        # Compute profit factor from winners / (trades - winners)
        total_trades = int(row.get("total_trades") or 0)
        total_winners = int(row.get("total_winners") or 0)
        losers = total_trades - total_winners
        if losers > 0 and total_winners > 0:
            # Simple approximation: winners:losers ratio as proxy for PF
            row["profit_factor"] = round(total_winners / losers, 2)
        else:
            row["profit_factor"] = None
        # Coerce Decimal types from postgres to float
        for key in ("total_net_pnl", "win_rate_pct", "total_net_pnl"):
            if row.get(key) is not None:
                row[key] = float(row[key])
        result.append(row)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Live positions
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/live_positions")
async def live_positions():
    """Open real trades (from DB is_open=TRUE) + open virtual trades (outcome=OPEN).

    Unrealised P&L for real trades comes from the MT5 bridge /positions endpoint.
    If the bridge is unreachable, stored gross_pnl is used as a fallback.
    """
    result: dict[str, Any] = {"real": [], "shadow": [], "bridge_live": False}

    # Real positions
    try:
        real_db = db.get_open_real_trades()
    except Exception as exc:
        log.warning("get_open_real_trades failed: %s", exc)
        real_db = []

    # Enrich with live unrealised P&L from bridge
    bridge_positions = _bridge_get("/positions") or []
    bridge_map: dict[int, dict] = {}
    if isinstance(bridge_positions, list):
        result["bridge_live"] = True
        bridge_map = {int(p["ticket"]): p for p in bridge_positions}

    for trade in real_db:
        ticket = int(trade.get("ticket", 0))
        if ticket in bridge_map:
            trade["unrealised_pnl"] = bridge_map[ticket].get("profit")
        else:
            trade["unrealised_pnl"] = trade.get("gross_pnl")
        # Coerce datetimes to ISO strings
        for k in ("opened_at", "closed_at", "updated_at"):
            if hasattr(trade.get(k), "isoformat"):
                trade[k] = trade[k].isoformat()
        result["real"].append(trade)

    # Shadow positions (open virtual trades)
    try:
        shadow_open = db.get_open_virtual_trades()
    except Exception as exc:
        log.warning("get_open_virtual_trades failed: %s", exc)
        shadow_open = []

    for trade in shadow_open:
        for k in ("signal_ts", "opened_at", "closed_at"):
            if hasattr(trade.get(k), "isoformat"):
                trade[k] = trade[k].isoformat()
        result["shadow"].append(trade)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Set strategy mode
# ─────────────────────────────────────────────────────────────────────────────

class SetModeRequest(BaseModel):
    mode: str


@app.post("/api/strategy/{name}/set_mode")
async def set_mode(name: str, body: SetModeRequest):
    """Change a strategy's mode.

    When mode=live, calls swap_live_strategy() — the ONLY safe way to promote.
    For shadow/off, calls set_strategy_mode() directly (no demotion needed).
    """
    mode = body.mode
    if mode not in ("live", "shadow", "off"):
        raise HTTPException(status_code=422, detail=f"Invalid mode {mode!r}; must be live, shadow, or off")

    try:
        if mode == "live":
            db.swap_live_strategy(name)
        else:
            db.set_strategy_mode(name, mode)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        log.error("set_mode failed for %s -> %s: %s", name, mode, exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return {"strategy": name, "mode": mode, "ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# Account info passthrough
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/account")
async def account():
    """Proxy to MT5 bridge /account for use by the top bar."""
    data = _bridge_get("/account")
    if data is None:
        return {"balance": None, "equity": None, "margin": None, "free_margin": None, "bridge_live": False}
    data["bridge_live"] = True
    return data
