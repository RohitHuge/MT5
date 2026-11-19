"""Shared data models used by every service in the platform.

These are the canonical types that cross service boundaries:
  - mt5-bridge produces Candle and OrderResult
  - candle-feed republishes Candle via Redis
  - strategy containers consume Candle and produce Signal
  - executor consumes Signal (live) and produces OrderResult
  - shadow-tracker consumes Signal (shadow)

No external dependencies — stdlib dataclasses only so any service can import
this file without a heavy requirements.txt.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


@dataclass
class Candle:
    """One closed OHLCV candle from MT5."""
    symbol: str
    timeframe: str        # "M1", "M5", "M15", "H1", etc.
    timestamp: datetime   # UTC, timezone-aware — candle open time
    open: float
    high: float
    low: float
    close: float
    volume: int
    spread: int           # points at candle open

    def to_json(self) -> str:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return json.dumps(d)

    @classmethod
    def from_json(cls, raw: str) -> "Candle":
        d = json.loads(raw)
        d["timestamp"] = datetime.fromisoformat(d["timestamp"])
        if d["timestamp"].tzinfo is None:
            d["timestamp"] = d["timestamp"].replace(tzinfo=timezone.utc)
        return cls(**d)


@dataclass
class Signal:
    """A trade signal emitted by a strategy.

    mode="live"   → executor will place a real MT5 order
    mode="shadow" → shadow-tracker will record a virtual trade
    """
    strategy: str         # "ema-scalp", "ob-fvg", "liq-sweep"
    symbol: str
    direction: str        # "BUY" or "SELL"
    entry_price: float
    sl_price: float
    tp_price: float
    lots: float
    confidence: float     # 0.0–1.0, strategy-defined heuristic
    candle_ts: datetime   # timestamp of the candle that fired the signal
    emitted_at: datetime  # wall-clock UTC time the signal was emitted
    mode: str             # "live" or "shadow"
    comment: str = ""     # optional human-readable reason

    def to_json(self) -> str:
        d = asdict(self)
        d["candle_ts"] = self.candle_ts.isoformat()
        d["emitted_at"] = self.emitted_at.isoformat()
        return json.dumps(d)

    @classmethod
    def from_json(cls, raw: str) -> "Signal":
        d = json.loads(raw)
        for key in ("candle_ts", "emitted_at"):
            d[key] = datetime.fromisoformat(d[key])
            if d[key].tzinfo is None:
                d[key] = d[key].replace(tzinfo=timezone.utc)
        return cls(**d)


@dataclass
class OrderResult:
    """Result of a real order sent to MT5 via the bridge."""
    ticket: int
    symbol: str
    direction: str
    lots: float
    fill_price: float
    sl_price: float
    tp_price: float
    filled_at: datetime
    comment: str
    error: str | None = None   # None means success

    @property
    def success(self) -> bool:
        return self.error is None

    def to_json(self) -> str:
        d = asdict(self)
        d["filled_at"] = self.filled_at.isoformat()
        return json.dumps(d)

    @classmethod
    def from_json(cls, raw: str) -> "OrderResult":
        d = json.loads(raw)
        d["filled_at"] = datetime.fromisoformat(d["filled_at"])
        if d["filled_at"].tzinfo is None:
            d["filled_at"] = d["filled_at"].replace(tzinfo=timezone.utc)
        return cls(**d)
