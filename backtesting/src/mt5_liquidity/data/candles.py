"""Candle data model and M5-to-M15 aggregation."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Candle:
    timestamp: datetime  # must be UTC-aware
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    spread: float = 0.0
    timeframe: str = "M5"

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError(
                f"Candle timestamp must be UTC-aware, got naive datetime: {self.timestamp}. "
                "Use datetime(..., tzinfo=timezone.utc) or datetime.fromtimestamp(ts, tz=timezone.utc)."
            )


@dataclass
class AggregationResult:
    """Return type for aggregate_m5_to_m15."""
    candles: list[Candle]
    incomplete_windows: list[dict]  # windows with < 3 M5 candles


def _m15_boundary(ts: datetime) -> datetime:
    """Return the floor 15-minute boundary for a given UTC timestamp."""
    minute_floor = (ts.minute // 15) * 15
    return ts.replace(minute=minute_floor, second=0, microsecond=0)


def aggregate_m5_to_m15(candles: list[Candle]) -> AggregationResult:
    """
    Group M5 candles into M15 candles by aligning on 15-minute boundaries.

    Rules:
    - Only group candles whose timestamps fall in the SAME 15-min boundary window.
    - A complete window has exactly 3 M5 candles.
    - Incomplete windows (< 3 candles) are reported in AggregationResult.incomplete_windows,
      NOT silently dropped.
    - Candles within a window must be chronologically ordered; raises ValueError otherwise.

    M15 OHLC: open=first, high=max, low=min, close=last.
    volume=sum, spread=max (conservative).
    timestamp = the 15-minute boundary (floor).
    """
    if not candles:
        return AggregationResult(candles=[], incomplete_windows=[])

    result: list[Candle] = []
    incomplete: list[dict] = []
    windows: dict[datetime, list[Candle]] = {}

    for c in candles:
        boundary = _m15_boundary(c.timestamp)
        windows.setdefault(boundary, []).append(c)

    for boundary in sorted(windows):
        group = windows[boundary]

        # Validate exact M5 cadence, not just the number/order of bars.
        expected = [boundary + timedelta(minutes=offset) for offset in (0, 5, 10)]
        actual = [c.timestamp.astimezone(timezone.utc) for c in group]
        if len(group) == 3 and actual != expected:
            raise ValueError(
                f"M5 candles do not match expected 5-minute slots in {boundary.isoformat()}: "
                f"expected {[t.isoformat() for t in expected]}, got {[t.isoformat() for t in actual]}"
            )

        # Validate intra-window ordering
        for i in range(1, len(group)):
            if group[i].timestamp <= group[i - 1].timestamp:
                raise ValueError(
                    f"Out-of-order M5 candles in M15 window {boundary.isoformat()}: "
                    f"{group[i - 1].timestamp.isoformat()} followed by {group[i].timestamp.isoformat()}"
                )

        if len(group) == 3:
            result.append(_make_m15(group, boundary))
        else:
            incomplete.append({
                "window_start": boundary,
                "expected": 3,
                "got": len(group),
                "candles": group,
            })

    return AggregationResult(candles=result, incomplete_windows=incomplete)


def _make_m15(group: list[Candle], boundary: datetime) -> Candle:
    return Candle(
        timestamp=boundary,
        open=group[0].open,
        high=max(c.high for c in group),
        low=min(c.low for c in group),
        close=group[-1].close,
        volume=sum(c.volume for c in group),
        spread=max(c.spread for c in group),
        timeframe="M15",
    )
