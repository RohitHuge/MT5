"""Shared FastAPI plumbing for the strategy services' /backtest endpoints.

Each strategy service keeps its Redis loop on the main thread and runs this HTTP
server on a daemon thread, so the container still starts with `python main.py`.
The signal detection lives in each service — this module only wires HTTP.
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Callable

from .models import Candle

log = logging.getLogger(__name__)

# Models defined at module level so FastAPI can introspect them correctly.
# Locally-scoped Pydantic models cause FastAPI to treat body params as query params.
try:
    from fastapi import Body, FastAPI
    from pydantic import BaseModel

    class BacktestRequest(BaseModel):
        candles: list[dict]
        params: dict = {}

    class BacktestResponse(BaseModel):
        trades: list[dict]

    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False


def candles_from_dicts(
    rows: list[dict],
    symbol: str = "XAUUSD",
    timeframe: str = "M5",
) -> list[Candle]:
    """Convert the dashboard's JSON candles into the internal Candle model."""
    out: list[Candle] = []
    for row in rows:
        ts = row.get("timestamp")
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if not isinstance(ts, datetime):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        try:
            out.append(
                Candle(
                    symbol=str(row.get("symbol") or symbol),
                    timeframe=str(row.get("timeframe") or timeframe),
                    timestamp=ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=int(row.get("volume") or 0),
                    spread=int(row.get("spread") or 0),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("Skipping malformed backtest candle: %s", exc)
    return out


def build_app(
    name: str,
    handler: Callable[[list[Candle], dict], list[dict]],
) -> Any:
    """Create the service's FastAPI app (None when fastapi is not installed)."""
    if not _FASTAPI_AVAILABLE:
        log.warning("Backtest HTTP disabled (fastapi/pydantic missing)")
        return None

    app = FastAPI(title=f"{name} backtest", version="1.0.0")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "strategy": name}

    @app.post("/backtest", response_model=BacktestResponse)
    async def backtest(body: BacktestRequest = Body(...)) -> Any:
        candles = candles_from_dicts(body.candles)
        try:
            trades = handler(candles, body.params or {})
        except Exception as exc:
            log.exception("backtest failed for %s: %s", name, exc)
            return BacktestResponse(trades=[])
        return BacktestResponse(trades=trades)

    return app


def start_in_thread(app: Any) -> threading.Thread | None:
    """Serve *app* on HTTP_PORT (default 8080) as a daemon thread."""
    if app is None:
        return None

    port = int(os.getenv("HTTP_PORT", "8080"))

    def _serve() -> None:
        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")

    thread = threading.Thread(target=_serve, daemon=True, name="backtest-http")
    thread.start()
    log.info("Backtest HTTP server listening on :%d", port)
    return thread
