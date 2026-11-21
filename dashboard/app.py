"""Dashboard service — FastAPI backend for the MT5 multi-strategy platform.

Endpoints:
  GET  /                                       React SPA (dashboard/frontend/dist)
  GET  /api/health                             DB + Redis + bridge connectivity
  GET  /api/strategies                         all strategies + today's P&L
  GET  /api/strategy/{name}/performance        daily rows (default last 30 days)
  GET  /api/strategy/{name}/equity_curve       cumulative equity series
  GET  /api/strategy/{name}/snapshots          indicator snapshots (chart overlays)
  GET  /api/compare                            all strategies side-by-side
  GET  /api/live_positions                     open real + shadow positions
  POST /api/strategy/{name}/set_mode           promote / demote strategy
  POST /api/strategies/register                strategy services self-register
  GET  /api/account                            MT5 account summary passthrough
  GET  /api/candles                            proxy bridge /candles
  GET  /api/candles/bulk                       paginated bridge candles for a date range
  GET  /api/symbol/{symbol}                    proxy bridge /symbol/{symbol}
  POST /api/order                              proxy bridge /order
  POST /api/close/{ticket}                     proxy bridge /close/{ticket}
  POST /api/backtest/run                       run a backtest on a strategy service
  GET  /api/backtest/history                   past backtest runs
  WS   /ws/signals                             live signal feed (Redis pubsub)
  WS   /ws/candles                             proxy bridge /ws/candles

All DB calls go through shared.db which owns the connection pool.
Bridge calls are plain requests.get() with a short timeout — if the bridge
is unreachable the endpoint returns a degraded-but-valid response instead of
crashing, so the dashboard stays usable even when MT5 is offline.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import threading
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import redis as redis_lib
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# websockets ships as a transitive dep of uvicorn[standard]; a missing install
# only disables the /ws/candles proxy, not the whole dashboard.
try:
    import websockets
except ImportError:  # pragma: no cover
    websockets = None  # type: ignore[assignment]

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

FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"

MT5_BRIDGE_URL = os.getenv("MT5_BRIDGE_URL", "http://host.docker.internal:8000")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")

# Display names for strategies seeded into DB on startup.
# Adding a new strategy: add one entry here + a docker-compose service named
# "strategy-{name}" on port 8080. No other dashboard changes needed.
STRATEGY_DISPLAY_NAMES: dict[str, str] = {
    "ema-scalp":     "EMA Scalp",
    "ob-fvg":        "OB+FVG",
    "liq-sweep":     "Liq Sweep",
    "oco-dual-stop": "OCO Dual Stop",
}


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    """Seed every known strategy into the DB on startup.

    Strategy containers register themselves via HTTP, but DNS is not always
    ready at container start time. Seeding here (same process, same DB) ensures
    the sidebar and Backtest dropdown are populated immediately.
    Mode is never overwritten — an existing strategy keeps its current mode.
    """
    for name, display in STRATEGY_DISPLAY_NAMES.items():
        try:
            db.register_strategy(name, display, "", "shadow")
            log.info("Seeded strategy into DB: %s", name)
        except Exception as exc:
            log.warning("Could not seed strategy %s: %s", name, exc)
    yield


app = FastAPI(title="MT5 Dashboard", version="2.0.0", lifespan=_lifespan)


def _bridge_ws_base() -> str:
    """Derive the bridge WebSocket origin from MT5_BRIDGE_URL (http→ws, https→wss)."""
    base = MT5_BRIDGE_URL.rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base[len("https://"):]
    if base.startswith("http://"):
        return "ws://" + base[len("http://"):]
    return "ws://" + base


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
# Bridge proxies (candles / symbol / order / close)
#
# The React SPA talks to this service only; these re-expose the bridge on the
# single dashboard origin.  Every one degrades instead of 500-ing when the
# bridge is offline (it lives on the Windows host and is frequently down).
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/candles")
async def api_candles(symbol: str = "XAUUSD", timeframe: str = "M5", count: int = 500):
    """Proxy bridge /candles. Returns [] when the bridge is unreachable."""
    params = urlencode({"symbol": symbol, "timeframe": timeframe, "count": count})
    data = _bridge_get(f"/candles?{params}")
    if data is None:
        return []
    if isinstance(data, dict):
        return data.get("candles") or []
    return data


_BULK_CHUNK = 5000
_BULK_MAX_COUNT = 50000     # largest single request we will ever make to the bridge
_BULK_MAX_CHUNKS = 200      # defensive cap so a bad range can't loop forever


def _parse_ts(value: Any) -> datetime | None:
    """Parse a candle timestamp (ISO string or datetime) into an aware datetime."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse_bound(raw: str, *, end_of_day: bool) -> datetime | None:
    """Accept either an ISO date ("2025-01-01") or a full ISO datetime."""
    if not raw:
        return None
    raw = raw.strip()
    if "T" in raw or " " in raw:
        return _parse_ts(raw)
    try:
        d = date.fromisoformat(raw[:10])
    except ValueError:
        return _parse_ts(raw)
    if end_of_day:
        return datetime(d.year, d.month, d.day, 23, 59, 59, 999999, tzinfo=timezone.utc)
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _shape_candle(raw: dict, symbol: str, timeframe: str) -> dict:
    """Normalise a bridge candle to the {symbol, timeframe, ...OHLC} contract."""

    def _float(key: str) -> float:
        try:
            return float(raw.get(key))
        except (TypeError, ValueError):
            return 0.0

    def _int(key: str) -> int:
        try:
            return int(raw.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    return {
        "symbol": raw.get("symbol") or symbol,
        "timeframe": raw.get("timeframe") or timeframe,
        "timestamp": raw.get("timestamp"),
        "open": _float("open"),
        "high": _float("high"),
        "low": _float("low"),
        "close": _float("close"),
        "volume": _int("volume"),
        "spread": _int("spread"),
    }


def _fetch_bulk_candles(symbol: str, timeframe: str, from_iso: str, to_iso: str) -> list[dict]:
    """Page backwards through bridge /candles until *from_iso* is covered.

    The bridge exposes no offset or end-date parameter, so "paging backwards"
    means re-requesting with a larger count each round (5000, 10000, … capped
    at _BULK_MAX_COUNT). We stop as soon as the oldest bar returned is at or
    before the requested start, the bridge stops yielding older data, or the
    chunk cap is hit.
    """
    lo = _parse_bound(from_iso, end_of_day=False)
    hi = _parse_bound(to_iso, end_of_day=True)
    if lo is None or hi is None or hi < lo:
        log.warning("bulk candles: bad range %r → %r", from_iso, to_iso)
        return []

    collected: dict[datetime, dict] = {}
    prev_oldest: datetime | None = None

    for chunk in range(1, _BULK_MAX_CHUNKS + 1):
        count = min(_BULK_CHUNK * chunk, _BULK_MAX_COUNT)
        params = urlencode({"symbol": symbol, "timeframe": timeframe, "count": count})
        data = _bridge_get(f"/candles?{params}", timeout=30.0)
        if not data:
            break
        batch = data if isinstance(data, list) else (data.get("candles") or [])
        if not batch:
            break

        parsed: list[tuple[datetime, dict]] = []
        for c in batch:
            ts = _parse_ts(c.get("timestamp"))
            if ts is not None:
                parsed.append((ts, c))
                collected[ts] = c
        if not parsed:
            break

        parsed.sort(key=lambda p: p[0])
        oldest = parsed[0][0]
        if oldest <= lo:
            break                       # requested range fully covered
        if prev_oldest is not None and oldest >= prev_oldest:
            break                       # no older data available (EA 5000-bar clamp)
        if len(batch) < count or count >= _BULK_MAX_COUNT:
            break                       # bridge can't give us anything older
        prev_oldest = oldest

    in_range = [(ts, c) for ts, c in collected.items() if lo <= ts <= hi]
    in_range.sort(key=lambda p: p[0])
    return [_shape_candle(c, symbol, timeframe) for _, c in in_range]


@app.get("/api/candles/bulk")
async def api_candles_bulk(symbol: str, timeframe: str, from_iso: str, to_iso: str):
    """All candles between from_iso and to_iso (bulk-paged from the bridge)."""
    candles = _fetch_bulk_candles(symbol, timeframe, from_iso, to_iso)
    if not candles:
        log.warning("bulk candles: empty result for %s %s %s→%s "
                    "(bridge offline or no data in range)", symbol, timeframe, from_iso, to_iso)
    return candles


@app.get("/api/symbol/{symbol}")
async def api_symbol(symbol: str):
    """Proxy bridge /symbol/{symbol} (contract size, digits, spread, …)."""
    data = _bridge_get(f"/symbol/{quote(symbol, safe='')}")
    if data is None:
        return {"error": f"bridge unreachable for {symbol}", "symbol": symbol, "bridge_live": False}
    if isinstance(data, dict):
        data["bridge_live"] = True
    return data


@app.post("/api/order")
async def api_order(body: dict):
    """Proxy bridge /order — forwards the JSON body unchanged."""
    try:
        resp = requests.post(f"{MT5_BRIDGE_URL}/order", json=body, timeout=15.0)
    except Exception as exc:
        log.warning("Bridge order failed: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"error": f"bridge unreachable: {exc}", "bridge_live": False, "ok": False},
        )

    if resp.status_code >= 400:
        detail: Any = resp.text
        with contextlib.suppress(Exception):
            payload = resp.json()
            detail = payload.get("detail") or payload.get("error") or detail
        status = 503 if resp.status_code >= 500 else resp.status_code
        return JSONResponse(
            status_code=status,
            content={"error": detail, "bridge_live": resp.status_code < 500, "ok": False},
        )

    with contextlib.suppress(Exception):
        data = resp.json()
        if isinstance(data, dict):
            data.setdefault("bridge_live", True)
        return data
    return {"ok": True, "bridge_live": True}


@app.post("/api/close/{ticket}")
async def api_close(ticket: int):
    """Proxy bridge /close/{ticket} — closes an open position."""
    try:
        resp = requests.post(f"{MT5_BRIDGE_URL}/close/{ticket}", timeout=15.0)
    except Exception as exc:
        log.warning("Bridge close %s failed: %s", ticket, exc)
        return JSONResponse(
            status_code=503,
            content={"error": f"bridge unreachable: {exc}", "bridge_live": False,
                     "ticket": ticket, "ok": False},
        )

    if resp.status_code >= 400:
        detail: Any = resp.text
        with contextlib.suppress(Exception):
            payload = resp.json()
            detail = payload.get("detail") or payload.get("error") or detail
        status = 503 if resp.status_code >= 500 else resp.status_code
        return JSONResponse(
            status_code=status,
            content={"error": detail, "bridge_live": resp.status_code < 500,
                     "ticket": ticket, "ok": False},
        )

    with contextlib.suppress(Exception):
        data = resp.json()
        if isinstance(data, dict):
            data.setdefault("bridge_live", True)
        return data
    return {"ok": True, "ticket": ticket, "bridge_live": True}


# ─────────────────────────────────────────────────────────────────────────────
# Backtesting
# ─────────────────────────────────────────────────────────────────────────────

class BacktestRequest(BaseModel):
    strategy: str
    symbol: str = "XAUUSD"
    timeframe: str = "M5"
    from_date: str
    to_date: str
    params: dict = Field(default_factory=dict)


def _compute_backtest_metrics(trades: list[dict]) -> dict:
    """Aggregate a simulated trade list into the backtest_runs metric set.

    net_pnl is treated as the per-trade return series: max_drawdown is the
    largest peak-to-trough fall of the cumulative P&L curve, and Sharpe is the
    mean/std of the per-trade P&L (non-annualised). win_rate is a 0–1 fraction,
    matching daily_summary.win_rate.
    """
    pnls: list[float] = []
    for t in trades:
        try:
            pnls.append(float(t.get("net_pnl") or 0.0))
        except (TypeError, ValueError):
            pnls.append(0.0)

    total = len(pnls)
    winners = sum(1 for p in pnls if p > 0)
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))

    equity = peak = max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    sharpe: float | None = None
    if total > 1:
        mean = sum(pnls) / total
        variance = sum((p - mean) ** 2 for p in pnls) / (total - 1)
        std = math.sqrt(variance)
        if std > 0:
            sharpe = round(mean / std, 3)

    return {
        "total_trades": total,
        "winners": winners,
        "win_rate": round(winners / total, 4) if total else 0.0,
        "net_pnl": round(sum(pnls), 2),
        "max_drawdown": round(max_dd, 2),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        "sharpe": sharpe,
    }


@app.post("/api/backtest/run")
async def backtest_run(body: BacktestRequest):
    """Run a backtest on the matching strategy service and persist the result.

    Strategy service URL is derived by convention: http://strategy-{name}:8080.
    Adding a new strategy requires only a docker-compose service named
    "strategy-{name}" — no code change here.
    """
    if not body.strategy or "/" in body.strategy:
        return JSONResponse(
            status_code=400,
            content={"error": f"invalid strategy name {body.strategy!r}", "ok": False},
        )
    base_url = f"http://strategy-{body.strategy}:8080"

    candles = _fetch_bulk_candles(body.symbol, body.timeframe, body.from_date, body.to_date)
    if not candles:
        return {
            "error": "no candles available for the requested range (bridge offline or empty range)",
            "ok": False,
            "bridge_live": False,
            "strategy": body.strategy,
            "symbol": body.symbol,
            "timeframe": body.timeframe,
            "from_date": body.from_date,
            "to_date": body.to_date,
            "trade_log": [],
        }

    try:
        resp = requests.post(
            f"{base_url.rstrip('/')}/backtest",
            json={"candles": candles, "params": body.params},
            timeout=180.0,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        log.warning("Backtest call to %s failed: %s", base_url, exc)
        return {
            "error": f"strategy service unreachable: {exc}",
            "ok": False,
            "strategy": body.strategy,
            "symbol": body.symbol,
            "timeframe": body.timeframe,
            "from_date": body.from_date,
            "to_date": body.to_date,
            "candles_used": len(candles),
            "trade_log": [],
        }

    trades = payload.get("trades") if isinstance(payload, dict) else payload
    if not isinstance(trades, list):
        trades = []
    if isinstance(payload, dict) and payload.get("error"):
        return {
            "error": payload["error"],
            "ok": False,
            "strategy": body.strategy,
            "symbol": body.symbol,
            "timeframe": body.timeframe,
            "candles_used": len(candles),
            "trade_log": trades,
        }

    metrics = _compute_backtest_metrics(trades)

    run_id: int | None = None
    try:
        run_id = db.save_backtest_run(
            body.strategy, body.symbol, body.timeframe,
            body.from_date[:10], body.to_date[:10],
            body.params, metrics, trades,
        )
    except Exception as exc:
        log.warning("Could not persist backtest run: %s", exc)

    return {
        "ok": True,
        "run_id": run_id,
        "strategy": body.strategy,
        "symbol": body.symbol,
        "timeframe": body.timeframe,
        "from_date": body.from_date,
        "to_date": body.to_date,
        "candles_used": len(candles),
        "metrics": metrics,
        **metrics,
        "trade_log": trades,
    }


@app.get("/api/backtest/history")
async def backtest_history(strategy: str | None = None, limit: int = 20):
    """Past backtest runs, newest first. Returns [] when the DB is unavailable."""
    try:
        return db.get_backtest_history(strategy=strategy, limit=limit)
    except Exception as exc:
        log.warning("backtest history query failed: %s", exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Strategy snapshots (indicator overlays)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/strategy/{name}/snapshots")
async def strategy_snapshots(name: str, limit: int = 200):
    """Last N indicator snapshots for a strategy. Returns [] when unavailable."""
    try:
        return db.get_snapshots(name, limit=limit)
    except Exception as exc:
        log.warning("snapshot query failed for %s: %s", name, exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Strategy trade history (individual rows, closed trades included)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_iso_param(value: str | None) -> datetime | None:
    """Parse an optional ISO timestamp query param; None when absent or invalid."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        log.warning("Ignoring unparseable ISO timestamp %r", value)
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@app.get("/api/strategy/{name}/trades")
async def strategy_trades(
    name: str,
    limit: int = 200,
    offset: int = 0,
    outcome: str | None = None,
    from_iso: str | None = None,
    to_iso: str | None = None,
):
    """Individual virtual trades for a strategy, newest signal first.

    Unlike /api/live_positions this includes closed trades, so the dashboard can
    render a signal-history table with outcome and realised P&L. The {rows,total}
    envelope carries the pagination total.
    """
    empty = {"rows": [], "total": 0}
    from_ts = _parse_iso_param(from_iso)
    to_ts = _parse_iso_param(to_iso)

    try:
        rows = db.get_virtual_trades(
            name,
            limit=limit,
            offset=offset,
            outcome=outcome,
            from_ts=from_ts,
            to_ts=to_ts,
        )
        total = db.count_virtual_trades(
            name, outcome=outcome, from_ts=from_ts, to_ts=to_ts
        )
    except Exception as exc:
        log.warning("virtual trade query failed for %s: %s", name, exc)
        return empty

    return {"rows": rows, "total": total}



# ─────────────────────────────────────────────────────────────────────────────
# Strategy self-registration
# ─────────────────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    name: str
    display_name: str
    description: str = ""
    mode: str = "shadow"


@app.post("/api/strategies/register")
async def strategies_register(body: RegisterRequest):
    """Upsert a strategy row from a strategy service's startup call.

    Mode is preserved for existing rows — a strategy can never promote itself.
    """
    try:
        current_mode = db.register_strategy(
            body.name, body.display_name, body.description, body.mode
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        log.warning("register_strategy failed for %s: %s", body.name, exc)
        return {"registered": False, "mode": body.mode, "error": str(exc)}

    return {"registered": True, "mode": current_mode}


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


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket — candle stream proxy (browser ← dashboard ← mt5-bridge)
# ─────────────────────────────────────────────────────────────────────────────

@app.websocket("/ws/candles")
async def ws_candles_proxy(websocket: WebSocket):
    """Relay the bridge's candle WebSocket to the browser.

    symbol/timeframe query params are forwarded as-is. If the bridge cannot be
    reached the client socket is closed cleanly instead of raising.
    """
    await websocket.accept()

    forwarded = {k: v for k, v in websocket.query_params.items()
                 if k in ("symbol", "timeframe")}
    upstream_url = f"{_bridge_ws_base()}/ws/candles"
    if forwarded:
        upstream_url = f"{upstream_url}?{urlencode(forwarded)}"

    if websockets is None:
        log.warning("ws_candles proxy unavailable: websockets library not installed")
        with contextlib.suppress(Exception):
            await websocket.close(code=1011, reason="websocket proxy unavailable")
        return

    try:
        upstream = await websockets.connect(upstream_url, open_timeout=5.0)
    except Exception as exc:
        log.warning("ws_candles: cannot reach bridge at %s: %s", upstream_url, exc)
        with contextlib.suppress(Exception):
            await websocket.close(code=1011, reason="bridge unreachable")
        return

    async def client_to_bridge() -> None:
        while True:
            message = await websocket.receive_text()
            await upstream.send(message)

    async def bridge_to_client() -> None:
        async for message in upstream:
            if isinstance(message, bytes):
                await websocket.send_bytes(message)
            else:
                await websocket.send_text(message)

    pump_client = asyncio.create_task(client_to_bridge())
    pump_bridge = asyncio.create_task(bridge_to_client())

    try:
        done, pending = await asyncio.wait(
            {pump_client, pump_bridge}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in done:
            if task.cancelled():
                continue
            exc = task.exception()
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                log.debug("ws_candles relay ended: %s", exc)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.warning("ws_candles relay error: %s", exc)
    finally:
        for task in (pump_client, pump_bridge):
            task.cancel()
        with contextlib.suppress(Exception):
            await upstream.close()
        with contextlib.suppress(Exception):
            await websocket.close()


# ─────────────────────────────────────────────────────────────────────────────
# React SPA — served from dashboard/frontend/dist (built by Vite)
#
# The catch-all route is registered last so /api/* and /ws/* keep priority.
# It returns index.html for any unmatched path, which lets the client-side
# router handle deep links like /shadow or /strategy/ema-scalp on hard refresh.
# ─────────────────────────────────────────────────────────────────────────────

if (FRONTEND_DIST / "index.html").exists():
    app.mount(
        "/assets",
        StaticFiles(directory=str(FRONTEND_DIST / "assets")),
        name="assets",
    )

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        candidate = FRONTEND_DIST / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")

else:
    log.warning(
        "frontend/dist/index.html not found — run `npm run build` in dashboard/frontend/ "
        "to serve the React SPA"
    )
