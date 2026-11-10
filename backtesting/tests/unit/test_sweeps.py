"""Unit tests for M15 sweep detection (Phase 3 P3.2–P3.5)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mt5_liquidity.data.candles import Candle
from mt5_liquidity.strategy.models import (
    LiquidityZone,
    Pivot,
    PivotType,
    ZoneStatus,
    ZoneType,
)
from mt5_liquidity.strategy.sweeps import detect_sweeps

BASE = datetime(2025, 1, 6, 8, 0, tzinfo=timezone.utc)
M15 = timedelta(minutes=15)
POINT = 0.01
MIN_SWEEP = 5   # points


def _ts(i: int) -> datetime:
    return BASE + i * M15


def _candle(i: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(timestamp=_ts(i), open=o, high=h, low=l, close=c, timeframe="M15")


def _bsl_zone(created_at_index: int, upper: float, lower: float) -> LiquidityZone:
    pivot = Pivot(
        index=created_at_index - 2,
        confirmed_at_index=created_at_index,
        timestamp=_ts(created_at_index - 2),
        confirmed_at=_ts(created_at_index),
        pivot_type=PivotType.HIGH,
        price=(upper + lower) / 2,
        candle_high=upper,
        candle_low=lower,
    )
    return LiquidityZone(
        zone_id=f"BSL_{created_at_index:06d}",
        zone_type=ZoneType.BSL,
        upper=upper,
        lower=lower,
        source_pivot_indices=[pivot.index],
        source_pivots=[pivot],
        created_at_index=created_at_index,
        created_at=_ts(created_at_index),
    )


def _ssl_zone(created_at_index: int, upper: float, lower: float) -> LiquidityZone:
    pivot = Pivot(
        index=created_at_index - 2,
        confirmed_at_index=created_at_index,
        timestamp=_ts(created_at_index - 2),
        confirmed_at=_ts(created_at_index),
        pivot_type=PivotType.LOW,
        price=(upper + lower) / 2,
        candle_high=upper,
        candle_low=lower,
    )
    return LiquidityZone(
        zone_id=f"SSL_{created_at_index:06d}",
        zone_type=ZoneType.SSL,
        upper=upper,
        lower=lower,
        source_pivot_indices=[pivot.index],
        source_pivots=[pivot],
        created_at_index=created_at_index,
        created_at=_ts(created_at_index),
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_bsl_sweep_detected():
    """BSL zone: wick above upper + min_distance, close back inside → SweepSignal."""
    zone = _bsl_zone(created_at_index=2, upper=1010.10, lower=1009.90)
    # Candle at idx 3: wick to 1010.17 (> 1010.10 + 0.05), close at 1009.80
    candles = [
        _candle(0, 1000, 1000, 1000, 1000),
        _candle(1, 1000, 1000, 1000, 1000),
        _candle(2, 1000, 1000, 1000, 1000),
        _candle(3, 1009.80, 1010.17, 1009.70, 1009.80),  # sweep
    ]
    signals = detect_sweeps(candles, [zone], MIN_SWEEP, POINT)
    assert len(signals) == 1
    s = signals[0]
    assert s.zone_type == ZoneType.BSL
    assert s.sweep_candle_index == 3
    assert s.wick_extreme == pytest.approx(1010.17)
    assert s.close_price == pytest.approx(1009.80)
    assert zone.status == ZoneStatus.SWEPT
    assert zone.sweep_evidence is not None


def test_ssl_sweep_detected():
    """SSL zone: wick below lower + min_distance, close back inside → SweepSignal."""
    zone = _ssl_zone(created_at_index=2, upper=990.10, lower=989.90)
    # Candle at idx 3: wick to 989.83 (< 989.90 - 0.05), close at 990.10
    candles = [
        _candle(0, 1000, 1000, 1000, 1000),
        _candle(1, 1000, 1000, 1000, 1000),
        _candle(2, 1000, 1000, 1000, 1000),
        _candle(3, 990.10, 990.20, 989.83, 990.10),  # sweep
    ]
    signals = detect_sweeps(candles, [zone], MIN_SWEEP, POINT)
    assert len(signals) == 1
    s = signals[0]
    assert s.zone_type == ZoneType.SSL
    assert s.wick_extreme == pytest.approx(989.83)
    assert zone.status == ZoneStatus.SWEPT


def test_false_sweep_wick_no_min_distance():
    """Wick above upper but NOT by min_sweep_points → no signal."""
    zone = _bsl_zone(created_at_index=2, upper=1010.10, lower=1009.90)
    # Wick only 0.02 above upper (< 0.05 min), close inside
    candles = [
        _candle(0, 1000, 1000, 1000, 1000),
        _candle(1, 1000, 1000, 1000, 1000),
        _candle(2, 1000, 1000, 1000, 1000),
        _candle(3, 1009.80, 1010.12, 1009.70, 1009.80),
    ]
    signals = detect_sweeps(candles, [zone], MIN_SWEEP, POINT)
    assert signals == []
    assert zone.status == ZoneStatus.ACTIVE


def test_breakout_not_a_sweep():
    """Close above BSL upper → INVALIDATED in liquidity_zones (not a sweep here)."""
    zone = _bsl_zone(created_at_index=2, upper=1010.10, lower=1009.90)
    # Close well above upper — this is handled by build_zones as INVALIDATED,
    # but detect_sweeps won't mark it SWEPT (close > upper fails the close-back-inside check)
    candles = [
        _candle(0, 1000, 1000, 1000, 1000),
        _candle(1, 1000, 1000, 1000, 1000),
        _candle(2, 1000, 1000, 1000, 1000),
        _candle(3, 1009.80, 1011.00, 1009.70, 1010.50),  # close > upper
    ]
    signals = detect_sweeps(candles, [zone], MIN_SWEEP, POINT)
    assert signals == []
    # Zone remains ACTIVE from detect_sweeps perspective
    # (build_zones would have set INVALIDATED, but we bypassed that here)
    assert zone.status == ZoneStatus.ACTIVE


def test_already_swept_zone_skipped():
    """Zone already SWEPT → not swept again by a later candle."""
    zone = _bsl_zone(created_at_index=2, upper=1010.10, lower=1009.90)
    candles = [
        _candle(0, 1000, 1000, 1000, 1000),
        _candle(1, 1000, 1000, 1000, 1000),
        _candle(2, 1000, 1000, 1000, 1000),
        _candle(3, 1009.80, 1010.17, 1009.70, 1009.80),  # first sweep
        _candle(4, 1009.80, 1010.20, 1009.70, 1009.70),  # would sweep again
    ]
    signals = detect_sweeps(candles, [zone], MIN_SWEEP, POINT)
    assert len(signals) == 1   # only one, not two
    assert signals[0].sweep_candle_index == 3


def test_zone_not_yet_created_skipped():
    """Zone created at index 5; candle at index 4 → zone not visible (no look-ahead)."""
    zone = _bsl_zone(created_at_index=5, upper=1010.10, lower=1009.90)
    candles = [
        _candle(0, 1000, 1000, 1000, 1000),
        _candle(1, 1000, 1000, 1000, 1000),
        _candle(2, 1000, 1000, 1000, 1000),
        _candle(3, 1000, 1000, 1000, 1000),
        _candle(4, 1009.80, 1010.17, 1009.70, 1009.80),  # would sweep, but zone not yet created
        _candle(5, 1000, 1000, 1000, 1000),               # zone created here
    ]
    signals = detect_sweeps(candles, [zone], MIN_SWEEP, POINT)
    assert signals == []
    assert zone.status == ZoneStatus.ACTIVE


def test_incomplete_candle_not_used():
    """
    Sweep logic only acts on completed (fully-closed) candles.
    Candles in the list are treated as closed. We verify that a candle
    whose wick qualifies only appears in signals when the detection loop
    actually reaches it — the loop is strictly sequential (index-ordered),
    so candles earlier in the list that do NOT meet criteria do not trigger.
    """
    zone = _bsl_zone(created_at_index=2, upper=1010.10, lower=1009.90)
    # Three candles after zone creation; only the last one sweeps
    candles = [
        _candle(0, 1000, 1000, 1000, 1000),
        _candle(1, 1000, 1000, 1000, 1000),
        _candle(2, 1000, 1000, 1000, 1000),
        _candle(3, 1009.80, 1009.95, 1009.70, 1009.80),  # wick doesn't reach upper
        _candle(4, 1009.80, 1010.17, 1009.70, 1009.80),  # sweep candle (closed)
    ]
    signals = detect_sweeps(candles, [zone], MIN_SWEEP, POINT)
    assert len(signals) == 1
    assert signals[0].sweep_candle_index == 4   # only the closed sweep candle triggers
