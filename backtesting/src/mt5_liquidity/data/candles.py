"""Candle data model and M5-to-M15 aggregation."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Candle:
    timestamp: datetime  # UTC
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    spread: float = 0.0
    timeframe: str = "M5"

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            # Treat naive datetimes as UTC
            self.timestamp = self.timestamp.replace(tzinfo=timezone.utc)


def _m15_boundary(ts: datetime) -> datetime:
    """Return the floor 15-minute boundary for a given UTC timestamp."""
    minute_floor = (ts.minute // 15) * 15
    return ts.replace(minute=minute_floor, second=0, microsecond=0)


def aggregate_m5_to_m15(candles: list[Candle]) -> list[Candle]:
    """
    Group M5 candles into M15 candles by aligning on 15-minute boundaries.
    Only emit complete groups of exactly 3 consecutive M5 candles.
    M15 OHLC: open=first, high=max, low=min, close=last.
    volume=sum, spread=max (conservative).
    """
    if not candles:
        return []

    result: list[Candle] = []
    group: list[Candle] = []
    current_boundary: datetime | None = None

    for c in candles:
        boundary = _m15_boundary(c.timestamp)
        if current_boundary is None:
            current_boundary = boundary

        if boundary != current_boundary:
            if len(group) == 3:
                result.append(_make_m15(group))
            group = []
            current_boundary = boundary

        group.append(c)

    if len(group) == 3:
        result.append(_make_m15(group))

    return result


def _make_m15(group: list[Candle]) -> Candle:
    return Candle(
        timestamp=group[0].timestamp,
        open=group[0].open,
        high=max(c.high for c in group),
        low=min(c.low for c in group),
        close=group[-1].close,
        volume=sum(c.volume for c in group),
        spread=max(c.spread for c in group),
        timeframe="M15",
    )
