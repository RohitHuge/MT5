"""MT5 Bridge — FastAPI service exposing MT5 terminal over HTTP + WebSocket.

Runs natively on the Windows host (not in Docker). All containerised services
reach it at http://host.docker.internal:8000.

Start:
    uvicorn main:app --host 0.0.0.0 --port 8000

The MT5 Python API is not thread-safe, so all MT5 calls are serialised through
a single asyncio.Lock (_mt5_lock) and run in the default thread executor.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import MetaTrader5 as mt5
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

TIMEFRAME_MAP: dict[str, int] = {
    "M1":  mt5.TIMEFRAME_M1,
    "M5":  mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1":  mt5.TIMEFRAME_H1,
    "H4":  mt5.TIMEFRAME_H4,
    "D1":  mt5.TIMEFRAME_D1,
}

MAGIC_NUMBER = 234001          # tags every order placed by this platform
CANDLE_POLL_INTERVAL = 5       # seconds between MT5 candle polls
WS_PING_INTERVAL = 30          # seconds between WebSocket pings

# ─────────────────────────────────────────────────────────────────────────────
# MT5 thread-safety lock
# ─────────────────────────────────────────────────────────────────────────────

_mt5_lock: asyncio.Lock | None = None   # created inside the event loop


def get_mt5_lock() -> asyncio.Lock:
    global _mt5_lock
    if _mt5_lock is None:
        _mt5_lock = asyncio.Lock()
    return _mt5_lock


async def call_mt5(fn, *args, **kwargs):
    """Run a synchronous MT5 call in the thread executor under the shared lock."""
    loop = asyncio.get_event_loop()
    async with get_mt5_lock():
        return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket connection manager
# ─────────────────────────────────────────────────────────────────────────────

class ConnectionManager:
    """Manages WebSocket clients grouped by (symbol, timeframe).

    One background polling task is created per unique (symbol, timeframe) pair.
    The task is cancelled when the last client for that pair disconnects.
    """

    def __init__(self) -> None:
        self._clients: dict[tuple[str, str], set[WebSocket]] = {}
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}
        # tracks the timestamp of the CURRENT (open) bar; broadcast fires when it changes
        self._current_bar_ts: dict[tuple[str, str], datetime | None] = {}

    async def connect(self, ws: WebSocket, symbol: str, timeframe: str) -> None:
        await ws.accept()
        key = (symbol, timeframe)
        if key not in self._clients:
            self._clients[key] = set()
            self._current_bar_ts[key] = None
            task = asyncio.create_task(self._poll_loop(symbol, timeframe))
            self._tasks[key] = task
        self._clients[key].add(ws)
        logger.info("WS client connected: %s %s (total=%d)", symbol, timeframe, len(self._clients[key]))

    def disconnect(self, ws: WebSocket, symbol: str, timeframe: str) -> None:
        key = (symbol, timeframe)
        if key in self._clients:
            self._clients[key].discard(ws)
            logger.info("WS client disconnected: %s %s (remaining=%d)", symbol, timeframe, len(self._clients[key]))
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
        """Poll MT5 every CANDLE_POLL_INTERVAL seconds.

        Detects when the current bar's timestamp changes (= a new bar opened,
        meaning the previous bar just closed) then fetches and broadcasts that
        closed candle.
        """
        key = (symbol, timeframe)
        tf_const = TIMEFRAME_MAP.get(timeframe)
        if tf_const is None:
            logger.error("Unknown timeframe for WS poll: %s", timeframe)
            return

        logger.info("Candle poll task started: %s %s", symbol, timeframe)
        try:
            while True:
                await asyncio.sleep(CANDLE_POLL_INTERVAL)
                try:
                    # Fetch 2 most recent bars: [0]=current open, [1]=last closed
                    rates = await call_mt5(mt5.copy_rates_from_pos, symbol, tf_const, 0, 2)
                    if rates is None or len(rates) < 2:
                        continue

                    current_bar_ts = datetime.fromtimestamp(int(rates[0]["time"]), tz=timezone.utc)

                    if self._current_bar_ts[key] is None:
                        # First poll — seed the timestamp, don't broadcast yet
                        self._current_bar_ts[key] = current_bar_ts
                        continue

                    if current_bar_ts != self._current_bar_ts[key]:
                        # New bar opened → rates[1] is the bar that just closed
                        self._current_bar_ts[key] = current_bar_ts
                        closed = rates[1]
                        field_names = rates.dtype.names if hasattr(rates, "dtype") else []

                        candle_ts = datetime.fromtimestamp(int(closed["time"]), tz=timezone.utc)
                        candle = {
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "timestamp": candle_ts.isoformat(),
                            "open":  float(closed["open"]),
                            "high":  float(closed["high"]),
                            "low":   float(closed["low"]),
                            "close": float(closed["close"]),
                            "volume": int(closed["tick_volume"]) if "tick_volume" in field_names else 0,
                            "spread": int(closed["spread"]) if "spread" in field_names else 0,
                        }
                        await self.broadcast(symbol, timeframe, json.dumps(candle))
                        logger.debug("Broadcast closed candle %s %s ts=%s", symbol, timeframe, candle_ts)

                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("Candle poll error (%s %s): %s", symbol, timeframe, exc)

        except asyncio.CancelledError:
            logger.info("Candle poll task cancelled: %s %s", symbol, timeframe)


ws_manager = ConnectionManager()


# ─────────────────────────────────────────────────────────────────────────────
# App lifespan
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialise the asyncio lock inside the running event loop
    global _mt5_lock
    _mt5_lock = asyncio.Lock()

    path = os.getenv("MT5_TERMINAL_PATH", r"C:\Program Files\MetaTrader 5\terminal64.exe")
    ok = mt5.initialize(path=path)
    if ok:
        info = mt5.terminal_info()
        account = mt5.account_info()
        logger.info(
            "MT5 connected | build=%s | account=%s | broker=%s",
            info.build if info else "?",
            account.login if account else "?",
            account.company if account else "?",
        )
    else:
        logger.warning(
            "MT5 initialize failed: %s — bridge running in degraded mode (all endpoints return 503)",
            mt5.last_error(),
        )

    yield

    mt5.shutdown()
    logger.info("MT5 shutdown complete")


app = FastAPI(title="MT5 Bridge", version="1.0.0", lifespan=lifespan)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _require_terminal() -> None:
    """Raise HTTP 503 if the MT5 terminal is not currently connected."""
    info = mt5.terminal_info()
    if info is None or not info.connected:
        raise HTTPException(status_code=503, detail="MT5 terminal not connected")


def _rates_to_candle(r: Any, symbol: str, timeframe: str, field_names: tuple) -> dict:
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "timestamp": datetime.fromtimestamp(int(r["time"]), tz=timezone.utc).isoformat(),
        "open":   float(r["open"]),
        "high":   float(r["high"]),
        "low":    float(r["low"]),
        "close":  float(r["close"]),
        "volume": int(r["tick_volume"]) if "tick_volume" in field_names else 0,
        "spread": int(r["spread"]) if "spread" in field_names else 0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic models
# ─────────────────────────────────────────────────────────────────────────────

class OrderRequest(BaseModel):
    symbol:    str
    direction: str          = Field(..., pattern="^(BUY|SELL)$")
    lots:      float        = Field(..., gt=0)
    sl:        float        = Field(..., description="Stop loss price (absolute)")
    tp:        float        = Field(..., description="Take profit price (absolute)")
    comment:   str          = Field("mt5-bridge", max_length=31)


# ─────────────────────────────────────────────────────────────────────────────
# REST endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    info = await call_mt5(mt5.terminal_info)
    account = await call_mt5(mt5.account_info)
    connected = info is not None and info.connected
    body = {
        "status": "ok" if connected else "error",
        "terminal_connected": connected,
        "terminal_build": info.build if info else None,
        "server_time_utc": datetime.now(tz=timezone.utc).isoformat(),
    }
    return JSONResponse(content=body, status_code=200 if connected else 503)


@app.get("/account")
async def account():
    _require_terminal()
    acc = await call_mt5(mt5.account_info)
    if acc is None:
        raise HTTPException(status_code=503, detail="Could not retrieve account info")
    return {
        "login":           acc.login,
        "balance":         acc.balance,
        "equity":          acc.equity,
        "margin":          acc.margin,
        "free_margin":     acc.margin_free,
        "margin_level_pct": round(acc.margin_level, 2) if acc.margin_level else 0.0,
        "currency":        acc.currency,
    }


@app.get("/symbol/{symbol}")
async def symbol_info(symbol: str):
    _require_terminal()
    info = await call_mt5(mt5.symbol_info, symbol)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Symbol not found: {symbol}")
    return {
        "symbol":        info.name,
        "point":         info.point,
        "spread":        info.spread,
        "volume_min":    info.volume_min,
        "volume_max":    info.volume_max,
        "volume_step":   info.volume_step,
        "tick_value":    info.trade_tick_value,
        "tick_size":     info.trade_tick_size,
        "contract_size": info.trade_contract_size,
        "digits":        info.digits,
        "trade_allowed": info.trade_mode != 0,
    }


@app.get("/candles")
async def candles(symbol: str, timeframe: str, count: int = 500):
    _require_terminal()

    tf_const = TIMEFRAME_MAP.get(timeframe.upper())
    if tf_const is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown timeframe '{timeframe}'. Valid: {', '.join(TIMEFRAME_MAP)}",
        )

    count = min(count, 5000)
    # pos=1 so we skip the current open bar and return only closed candles
    rates = await call_mt5(mt5.copy_rates_from_pos, symbol, tf_const, 1, count)
    if rates is None or len(rates) == 0:
        raise HTTPException(status_code=503, detail=f"No candle data for {symbol} {timeframe}")

    field_names = rates.dtype.names if hasattr(rates, "dtype") else ()
    tf_upper = timeframe.upper()
    return [_rates_to_candle(r, symbol, tf_upper, field_names) for r in rates]


@app.get("/positions")
async def positions():
    _require_terminal()
    pos_list = await call_mt5(mt5.positions_get)
    if pos_list is None:
        return []
    result = []
    for p in pos_list:
        result.append({
            "ticket":        p.ticket,
            "symbol":        p.symbol,
            "direction":     "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
            "lots":          p.volume,
            "open_price":    p.price_open,
            "current_price": p.price_current,
            "sl":            p.sl,
            "tp":            p.tp,
            "profit":        p.profit,
            "swap":          p.swap,
            "commission":    p.commission,
            "opened_at":     datetime.fromtimestamp(p.time, tz=timezone.utc).isoformat(),
            "comment":       p.comment,
            "magic":         p.magic,
        })
    return result


@app.post("/order")
async def place_order(req: OrderRequest):
    _require_terminal()

    is_buy = req.direction == "BUY"
    order_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL

    tick = await call_mt5(mt5.symbol_info_tick, req.symbol)
    if tick is None:
        raise HTTPException(status_code=503, detail=f"Cannot get tick for {req.symbol}")

    price = tick.ask if is_buy else tick.bid
    now = datetime.now(tz=timezone.utc)

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       req.symbol,
        "volume":       req.lots,
        "type":         order_type,
        "price":        price,
        "sl":           req.sl,
        "tp":           req.tp,
        "deviation":    50,
        "magic":        MAGIC_NUMBER,
        "comment":      req.comment[:31],
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = await call_mt5(mt5.order_send, request)

    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info(
            "Order placed: %s %s %.2f lots @ %.5f | ticket=%d | comment=%s",
            req.direction, req.symbol, req.lots, result.price, result.order, req.comment,
        )
        return {
            "ticket":     result.order,
            "symbol":     req.symbol,
            "direction":  req.direction,
            "lots":       req.lots,
            "fill_price": result.price,
            "sl_price":   req.sl,
            "tp_price":   req.tp,
            "filled_at":  now.isoformat(),
            "comment":    req.comment,
            "error":      None,
        }

    error_msg = result.comment if result else str(mt5.last_error())
    retcode = result.retcode if result else -1
    logger.warning("Order rejected: %s %s | retcode=%d | %s", req.direction, req.symbol, retcode, error_msg)
    return {
        "ticket":     -1,
        "symbol":     req.symbol,
        "direction":  req.direction,
        "lots":       req.lots,
        "fill_price": 0.0,
        "sl_price":   req.sl,
        "tp_price":   req.tp,
        "filled_at":  now.isoformat(),
        "comment":    req.comment,
        "error":      f"RETCODE_{retcode}: {error_msg}",
    }


@app.post("/close/{ticket}")
async def close_position(ticket: int):
    _require_terminal()

    pos_list = await call_mt5(mt5.positions_get, ticket=ticket)
    if not pos_list:
        raise HTTPException(status_code=404, detail=f"Position {ticket} not found in open positions")

    pos = pos_list[0]
    is_buy = pos.type == mt5.ORDER_TYPE_BUY
    close_type = mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY

    tick = await call_mt5(mt5.symbol_info_tick, pos.symbol)
    if tick is None:
        raise HTTPException(status_code=503, detail=f"Cannot get tick for {pos.symbol}")

    price = tick.bid if is_buy else tick.ask

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       pos.symbol,
        "volume":       pos.volume,
        "type":         close_type,
        "position":     ticket,
        "price":        price,
        "deviation":    50,
        "magic":        MAGIC_NUMBER,
        "comment":      "bridge-close",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = await call_mt5(mt5.order_send, request)

    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info("Position closed: ticket=%d @ %.5f | profit=%.2f", ticket, result.price, pos.profit)
        return {
            "ticket":      ticket,
            "closed":      True,
            "close_price": result.price,
            "profit":      pos.profit,
            "error":       None,
        }

    error_msg = result.comment if result else str(mt5.last_error())
    retcode = result.retcode if result else -1
    logger.warning("Close failed: ticket=%d | retcode=%d | %s", ticket, retcode, error_msg)
    return {
        "ticket":      ticket,
        "closed":      False,
        "close_price": 0.0,
        "profit":      0.0,
        "error":       f"RETCODE_{retcode}: {error_msg}",
    }


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket endpoint
# ─────────────────────────────────────────────────────────────────────────────

@app.websocket("/ws/candles")
async def ws_candles(ws: WebSocket, symbol: str = "XAUUSD", timeframe: str = "M5"):
    timeframe = timeframe.upper()
    if timeframe not in TIMEFRAME_MAP:
        await ws.close(code=1008, reason=f"Unknown timeframe '{timeframe}'")
        return

    await ws_manager.connect(ws, symbol, timeframe)
    try:
        while True:
            # Keep the connection alive; actual sends happen from the poll task.
            # ping_interval/timeout is handled by uvicorn's websockets layer.
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
