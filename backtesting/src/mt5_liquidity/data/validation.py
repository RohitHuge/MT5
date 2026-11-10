"""Candle series validation — never silently drops bad data."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


from .candles import Candle

TIMEFRAME_INTERVALS = {
    "M5": timedelta(minutes=5),
    "M15": timedelta(minutes=15),
    "H1": timedelta(hours=1),
}

# XAUUSD weekend gap: Sat 00:00 UTC to Sun 22:00 UTC (approximate broker session)
_WEEKEND_GAP_HOURS = 46  # hours from Fri close ~22:00 to Sun open ~22:00


@dataclass
class ValidationResult:
    passed: bool
    candle_count: int
    duplicate_timestamps: list[datetime] = field(default_factory=list)
    ohlc_violations: list[tuple[datetime, str]] = field(default_factory=list)
    gaps: list[tuple[datetime, datetime]] = field(default_factory=list)
    out_of_order: list[datetime] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _is_weekend_gap(a: datetime, b: datetime) -> bool:
    """Return True if the gap between a and b is consistent with a forex weekend closure."""
    diff = b - a
    # Weekend gap is typically 44-50 hours
    return timedelta(hours=44) <= diff <= timedelta(hours=50)


def validate_candles(candles: list[Candle], timeframe: str = "M5") -> ValidationResult:
    """
    Validate a candle series for:
    1. Chronological order
    2. Duplicate timestamps
    3. OHLC invariants
    4. Unexpected gaps (weekend gaps are skipped)
    """
    result = ValidationResult(passed=True, candle_count=len(candles))

    if not candles:
        result.warnings.append("Empty candle series.")
        return result

    expected_interval = TIMEFRAME_INTERVALS.get(timeframe)
    seen_timestamps: set[datetime] = set()

    for i, c in enumerate(candles):
        ts = c.timestamp

        # 1. Duplicate timestamps
        if ts in seen_timestamps:
            result.duplicate_timestamps.append(ts)
            result.passed = False
        seen_timestamps.add(ts)

        # 2. Chronological order (guard against comparing naive vs aware)
        if i > 0:
            prev_ts = candles[i - 1].timestamp
            try:
                if ts <= prev_ts:
                    result.out_of_order.append(ts)
                    result.passed = False
            except TypeError:
                # Mixed naive/aware — record as warning, skip ordering check
                result.warnings.append(
                    f"Cannot compare timestamps at index {i}: "
                    f"mixed naive/aware ({prev_ts} vs {ts})"
                )

        # 3. OHLC invariants
        if c.high < c.open or c.high < c.close:
            result.ohlc_violations.append((ts, f"High {c.high} < max(O={c.open}, C={c.close})"))
            result.passed = False
        if c.low > c.open or c.low > c.close:
            result.ohlc_violations.append((ts, f"Low {c.low} > min(O={c.open}, C={c.close})"))
            result.passed = False
        if c.high < c.low:
            result.ohlc_violations.append((ts, f"High {c.high} < Low {c.low}"))
            result.passed = False

        # 3b. Timezone warning
        if c.timestamp.tzinfo is None:
            result.warnings.append(f"naive timestamp at {ts.isoformat()}")

        # 4. Gap detection
        if i > 0 and expected_interval is not None:
            prev_ts = candles[i - 1].timestamp
            diff = ts - prev_ts
            if diff > expected_interval and not _is_weekend_gap(prev_ts, ts):
                result.gaps.append((prev_ts, ts))
                result.warnings.append(
                    f"Gap detected: {prev_ts.isoformat()} -> {ts.isoformat()} ({diff})"
                )

    return result
