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
  WS   /ws/signals                             live signal feed (Redis pubsub)

All DB calls go through shared.db which owns the connection pool.
Bridge calls are plain requests.get() with a short timeout — if the bridge
is unreachable the endpoint returns a degraded-but-valid response instead of
crashing, so the dashboard stays usable even when MT5 is offline.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from datetime import date, datetime, timezone, timedelta
from typing import Any

import redis as redis_lib
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# shared/ lives one level up from dashboard/ inside the container (COPY shared/ /app/shared/)
import sys
sys.path.insert(0, "/app")

from shared import db
from shared.channels import SIGNAL_ALL

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
# Strategies list  (cached 60 s in Redis)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/strategies")
async def list_strategies():
    """Return all strategies with current mode and today's P&L (real + shadow).

    Result is cached in Redis under key 'cache:strategies' for 60 seconds to
    reduce DB load on every dashboard page load.
    """
    cache_key = "cache:strategies"

    # Cache read
    try:
        r = _redis_client()
        cached = r.get(cache_key)
        if cached:
            return JSONResponse(content=json.loads(cached))
    except Exception as exc:
        log.warning("Redis cache read failed (strategies): %s", exc)

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

    # Cache write — best-effort, don't fail the request if Redis is down
    try:
        r = _redis_client()
        r.set(cache_key, json.dumps(enriched, default=str), ex=60)
    except Exception as exc:
        log.warning("Redis cache write failed (strategies): %s", exc)

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
# Comparison  (cached 5 min in Redis per distinct `days` value)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/compare")
async def compare(days: int = 30):
    """All strategies aggregated over the last N days (for comparison table).

    Also computes week and month sub-totals so the table can show
    'this week' and 'this month' columns without extra queries.

    Result is cached in Redis under key 'cache:compare:{days}' for 300 seconds
    (5 minutes) to reduce DB load on the most expensive dashboard query.
    """
    cache_key = f"cache:compare:{days}"

    # Cache read
    try:
        rc = _redis_client()
        cached = rc.get(cache_key)
        if cached:
            return JSONResponse(content=json.loads(cached))
    except Exception as exc:
        log.warning("Redis cache read failed (compare): %s", exc)

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

    # Cache write — best-effort
    try:
        rc = _redis_client()
        rc.set(cache_key, json.dumps(result, default=str), ex=300)
    except Exception as exc:
        log.warning("Redis cache write failed (compare): %s", exc)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Live positions  (real + shadow with intraday floating P&L)
# ─────────────────────────────────────────────────────────────────────────────

# Multiplier for XAUUSD unrealised P&L calculation:
#   tick_value=1.0, point=0.01  →  price_diff / 0.01 * 1.0 * lots = lots * 100
_XAUUSD_MULTIPLIER = 100.0


@app.get("/api/live_positions")
async def live_positions():
    """Open real trades (from bridge) + open shadow virtual trades (from Postgres).

    For shadow positions, calls the bridge /candles endpoint to get the latest
    close price and computes an intraday unrealised P&L.

    If the bridge is unreachable all bridge-dependent fields are None / absent;
    the endpoint still returns shadow positions from the DB so the dashboard
    remains useful.
    """
    result: dict[str, Any] = {"real": [], "shadow": [], "bridge_live": False}

    # ── Real positions ────────────────────────────────────────────────────────
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
        trade["is_shadow"] = False
        # Coerce datetimes to ISO strings
        for k in ("opened_at", "closed_at", "updated_at"):
            if hasattr(trade.get(k), "isoformat"):
                trade[k] = trade[k].isoformat()
        result["real"].append(trade)

    # ── Shadow (virtual) open positions ──────────────────────────────────────
    try:
        shadow_open = db.get_open_virtual_trades()
    except Exception as exc:
        log.warning("get_open_virtual_trades failed: %s", exc)
        shadow_open = []

    # Fetch latest candle once — shared across all open shadow trades
    latest_candle = _bridge_get("/candles?symbol=XAUUSD&timeframe=M5&count=1")
    current_price: float | None = None
    if latest_candle:
        try:
            # Bridge returns either a list of candles or a dict with a "candles" key
            if isinstance(latest_candle, list) and latest_candle:
                current_price = float(latest_candle[-1].get("close", 0) or 0) or None
            elif isinstance(latest_candle, dict):
                candles = latest_candle.get("candles") or []
                if candles:
                    current_price = float(candles[-1].get("close", 0) or 0) or None
        except Exception as exc:
            log.warning("Could not parse latest candle: %s", exc)

    for trade in shadow_open:
        trade["is_shadow"] = True
        # Compute floating P&L
        try:
            if current_price is not None:
                entry = float(trade.get("entry_price") or 0)
                lots = float(trade.get("lots") or 0)
                direction = str(trade.get("direction", "")).upper()
                direction_sign = 1.0 if direction == "BUY" else -1.0
                trade["current_price"] = current_price
                trade["unrealised_pnl"] = round(
                    (current_price - entry) * direction_sign * lots * _XAUUSD_MULTIPLIER, 2
                )
            else:
                trade["current_price"] = None
                trade["unrealised_pnl"] = None
        except Exception as exc:
            log.warning("unrealised_pnl calc failed for virtual trade: %s", exc)
            trade["current_price"] = None
            trade["unrealised_pnl"] = None

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

    # Invalidate cached data that includes strategy modes
    try:
        rc = _redis_client()
        rc.delete("cache:strategies")
        rc.delete("cache:compare:7")
        rc.delete("cache:compare:30")
    except Exception:
        pass  # cache invalidation is best-effort

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


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket — live signal feed
# ─────────────────────────────────────────────────────────────────────────────

def _run_pubsub_thread(queue: asyncio.Queue, loop: asyncio.AbstractEventLoop, stop_event: threading.Event) -> None:
    """Background thread: subscribes to Redis signal channels and pushes
    messages onto *queue* using the asyncio event loop.

    Subscribes to:
      - SIGNAL_ALL  ("signals:all")  — all strategies publish here since the
        dual-live fix; executor and shadow-tracker both consume from this channel

    Runs until *stop_event* is set (triggered on WebSocket disconnect).
    """
    try:
        rc = redis_lib.from_url(REDIS_URL, decode_responses=True)
        pubsub = rc.pubsub()
        pubsub.subscribe(SIGNAL_ALL)

        for message in pubsub.listen():
            if stop_event.is_set():
                break
            if message["type"] != "message":
                continue
            data = message.get("data")
            if not data:
                continue
            # Enqueue onto the asyncio event loop from this sync thread
            loop.call_soon_threadsafe(queue.put_nowait, data)

    except Exception as exc:
        log.warning("pubsub thread error: %s", exc)
    finally:
        try:
            pubsub.unsubscribe()
            pubsub.close()
        except Exception:
            pass


@app.websocket("/ws/signals")
async def ws_signals(websocket: WebSocket):
    """WebSocket endpoint that streams live signals to the browser.

    Threading bridge:
      A background (daemon) thread runs a sync Redis pubsub loop and pushes
      raw JSON strings onto an asyncio.Queue via loop.call_soon_threadsafe().
      The async handler below drains that queue and forwards each message to
      the WebSocket client.  On disconnect a threading.Event signals the thread
      to stop subscribing.
    """
    await websocket.accept()

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue[str] = asyncio.Queue()
    stop_event = threading.Event()

    thread = threading.Thread(
        target=_run_pubsub_thread,
        args=(queue, loop, stop_event),
        daemon=True,
        name="ws-signals-pubsub",
    )
    thread.start()

    try:
        while True:
            # Wait for the next message from Redis (with a small timeout so we
            # can detect disconnects promptly even when no signals are flowing)
            try:
                raw = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                # Send a keepalive ping so the browser can detect stale connections
                try:
                    await websocket.send_text(json.dumps({"type": "ping"}))
                except Exception:
                    break
                continue

            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                payload = {"raw": raw}

            try:
                await websocket.send_text(json.dumps(payload))
            except Exception:
                break  # client disconnected

    except WebSocketDisconnect:
        pass
    finally:
        stop_event.set()
        log.debug("ws_signals: client disconnected, pubsub thread signalled to stop")
