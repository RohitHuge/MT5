"""MT5 Bridge — FastAPI service exposing MT5 terminal over HTTP + WebSocket.

Runs in Docker on Ubuntu. Calls the MT5Bridge MQL5 EA (socket server inside
MT5 terminal) via HTTP instead of the Windows-only MetaTrader5 Python package.

Environment variables:
    EA_URL          Base URL of the MQL5 EA  (default: http://localhost:8002)
    LOG_LEVEL       Logging level            (default: INFO)

Start:
    uvicorn main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mt5-bridge")

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

EA_URL             = os.getenv("EA_URL", "http://localhost:8002")
CANDLE_POLL_INTERVAL = 5    # seconds between candle polls
WS_PING_INTERVAL   = 30    # seconds between WebSocket pings

VALID_TIMEFRAMES = {"M1", "M5", "M15", "M30", "H1", "H4", "D1"}

# ─────────────────────────────────────────────────────────────────────────────
# EA HTTP client
# ─────────────────────────────────────────────────────────────────────────────

_ea: httpx.AsyncClient | None = None


def get_ea() -> httpx.AsyncClient:
    if _ea is None:
        raise RuntimeError("EA client not initialised")
    return _ea


async def call_ea(method: str, path: str, **kwargs) -> Any:
    """Call the MQL5 EA and return the parsed JSON body."""
    try:
        r = await get_ea().request(method, path, **kwargs)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code,
                            detail=exc.response.text)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503,
                            detail=f"EA unreachable: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket connection manager
# ─────────────────────────────────────────────────────────────────────────────

class ConnectionManager:
    """Manages WebSocket clients grouped by (symbol, timeframe).

    One background polling task per unique (symbol, timeframe) pair.
    Cancelled when the last client for that pair disconnects.
    """

    def __init__(self) -> None:
        self._clients:        dict[tuple[str, str], set[WebSocket]] = {}
        self._tasks:          dict[tuple[str, str], asyncio.Task]   = {}
        self._current_bar_ts: dict[tuple[str, str], datetime | None] = {}

    async def connect(self, ws: WebSocket, symbol: str, timeframe: str) -> None:
        await ws.accept()
        key = (symbol, timeframe)
        if key not in self._clients:
            self._clients[key] = set()
            self._current_bar_ts[key] = None
            self._tasks[key] = asyncio.create_task(
                self._poll_loop(symbol, timeframe)
            )
        self._clients[key].add(ws)
        logger.info("WS connect: %s %s (total=%d)",
                    symbol, timeframe, len(self._clients[key]))

    def disconnect(self, ws: WebSocket, symbol: str, timeframe: str) -> None:
        key = (symbol, timeframe)
        if key in self._clients:
            self._clients[key].discard(ws)
            logger.info("WS disconnect: %s %s (remaining=%d)",
                        symbol, timeframe, len(self._clients[key]))
            if not self._clients[key]:
                task = self._tasks.pop(key, None)
                if task:
                    task.cancel()
                del self._clients[key]
                self._current_bar_ts.pop(key, None)

    async def broadcast(self, symbol: str, timeframe: str, payload: str) -> None:
        key = (symbol, timeframe)
        dead: set[WebSocket] = set()
        for ws in list(self._clients.get(key, [])):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.disconnect(ws, symbol, timeframe)

    async def _poll_loop(self, symbol: str, timeframe: str) -> None:
        key = (symbol, timeframe)
        logger.info("Candle poll started: %s %s", symbol, timeframe)
        try:
            while True:
                await asyncio.sleep(CANDLE_POLL_INTERVAL)
                try:
                    candles = await call_ea(
                        "GET",
                        f"/candles?symbol={symbol}&timeframe={timeframe}&count=2",
                    )
                    if not candles or len(candles) < 2:
                        continue

                    # candles[0] is the oldest; candles[-1] is the newest closed bar
                    latest = candles[-1]
                    bar_ts = datetime.fromisoformat(
                        latest["timestamp"].replace("Z", "+00:00")
                    )

                    if self._current_bar_ts[key] is None:
                        self._current_bar_ts[key] = bar_ts
                        continue

                    if bar_ts != self._current_bar_ts[key]:
                        self._current_bar_ts[key] = bar_ts
                        await self.broadcast(symbol, timeframe, json.dumps(latest))
                        logger.debug("Broadcast candle %s %s ts=%s",
                                     symbol, timeframe, bar_ts)

                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("Poll error (%s %s): %s", symbol, timeframe, exc)

        except asyncio.CancelledError:
            logger.info("Candle poll cancelled: %s %s", symbol, timeframe)


ws_manager = ConnectionManager()


# ─────────────────────────────────────────────────────────────────────────────
# App lifespan
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _ea
    _ea = httpx.AsyncClient(base_url=EA_URL, timeout=10.0)

    # Probe the EA on startup — log a warning if unreachable but keep running
    try:
        health = await _ea.get("/health")
        data   = health.json()
        logger.info(
            "EA connected | build=%s | account_connected=%s",
            data.get("terminal_build", "?"),
            data.get("terminal_connected", "?"),
        )
    except Exception as exc:
        logger.warning(
            "EA unreachable at startup (%s) — bridge running in degraded mode", exc
        )

    yield

    await _ea.aclose()
    logger.info("EA client closed")


app = FastAPI(title="MT5 Bridge", version="2.0.0", lifespan=lifespan)


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic models
# ─────────────────────────────────────────────────────────────────────────────

class OrderRequest(BaseModel):
    symbol:    str
    direction: str   = Field(..., pattern="^(BUY|SELL)$")
    lots:      float = Field(..., gt=0)
    sl:        float = Field(..., description="Stop loss price (absolute)")
    tp:        float = Field(..., description="Take profit price (absolute)")
    comment:   str   = Field("mt5-bridge", max_length=31)


# ─────────────────────────────────────────────────────────────────────────────
# REST endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    try:
        data = await call_ea("GET", "/health")
        connected = data.get("terminal_connected", False)
        return JSONResponse(
            content={**data, "status": "ok" if connected else "error"},
            status_code=200 if connected else 503,
        )
    except HTTPException:
        return JSONResponse(
            content={"status": "error", "terminal_connected": False,
                     "detail": "EA unreachable"},
            status_code=503,
        )


@app.get("/account")
async def account():
    return await call_ea("GET", "/account")


@app.get("/symbol/{symbol}")
async def symbol_info(symbol: str):
    data = await call_ea("GET", f"/symbol/{symbol}")
    if "error" in data:
        raise HTTPException(status_code=404, detail=data["error"])
    return data


@app.get("/candles")
async def candles(symbol: str, timeframe: str, count: int = 500):
    tf = timeframe.upper()
    if tf not in VALID_TIMEFRAMES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown timeframe '{timeframe}'. Valid: {', '.join(sorted(VALID_TIMEFRAMES))}",
        )
    # NOTE: the MQL5 EA (experts/MT5Bridge.mq5, HandleCandles) independently clamps
    # count to 5000; lifting that ceiling needs a recompile of the EA. Bulk pagination
    # in the dashboard still works correctly at 5000-chunk granularity.
    count = min(count, 50000)
    return await call_ea("GET", f"/candles?symbol={symbol}&timeframe={tf}&count={count}")


@app.get("/positions")
async def positions():
    return await call_ea("GET", "/positions")


@app.post("/order")
async def place_order(req: OrderRequest):
    return await call_ea("POST", "/order", json=req.model_dump())


@app.post("/close/{ticket}")
async def close_position(ticket: int):
    data = await call_ea("POST", f"/close/{ticket}")
    if "error" in data and data["error"] and "not found" in str(data["error"]):
        raise HTTPException(status_code=404, detail=data["error"])
    return data


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket endpoint
# ─────────────────────────────────────────────────────────────────────────────

@app.websocket("/ws/candles")
async def ws_candles(ws: WebSocket, symbol: str = "XAUUSD", timeframe: str = "M5"):
    timeframe = timeframe.upper()
    if timeframe not in VALID_TIMEFRAMES:
        await ws.close(code=1008, reason=f"Unknown timeframe '{timeframe}'")
        return

    await ws_manager.connect(ws, symbol, timeframe)
    try:
        while True:
            await asyncio.sleep(WS_PING_INTERVAL)
            try:
                await ws.send_text(json.dumps({"type": "ping"}))
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(ws, symbol, timeframe)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )
