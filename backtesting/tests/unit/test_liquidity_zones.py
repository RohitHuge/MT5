"""Unit tests for liquidity zone construction."""
from __future__ import annotations

from datetime import timedelta

import pytest

from mt5_liquidity.data.candles import Candle
from mt5_liquidity.strategy.liquidity_zones import build_zones, get_zones_known_at
from mt5_liquidity.strategy.models import ZoneStatus, ZoneType
from mt5_liquidity.strategy.swings import detect_pivots
from tests.fixtures.candle_fixtures import (
    BASE_TIME,
    M5,
    flat_series,
    make_candle,
    with_swing_high,
    with_swing_low,
)

POINT = 0.01
TOLERANCE_POINTS = 10
TOLERANCE = TOLERANCE_POINTS * POINT  # 0.10


def _build(candles):
    pivots = detect_pivots(candles, left=2, right=2)
    return build_zones(candles, pivots, TOLERANCE_POINTS, POINT)


def test_one_swing_high_creates_one_bsl_zone():
    candles = with_swing_high(base_price=1000.0, swing_price=1010.0, swing_at=5, n=15)
    zones = _build(candles)
    bsl = [z for z in zones if z.zone_type == ZoneType.BSL]
    assert len(bsl) == 1
    z = bsl[0]
    assert abs(z.upper - (1010.0 + TOLERANCE)) < 1e-9
    assert abs(z.lower - (1010.0 - TOLERANCE)) < 1e-9


def test_one_swing_low_creates_one_ssl_zone():
    candles = with_swing_low(base_price=1000.0, swing_price=990.0, swing_at=5, n=15)
    zones = _build(candles)
    ssl = [z for z in zones if z.zone_type == ZoneType.SSL]
    assert len(ssl) == 1
    z = ssl[0]
    assert abs(z.upper - (990.0 + TOLERANCE)) < 1e-9
    assert abs(z.lower - (990.0 - TOLERANCE)) < 1e-9


def test_two_equal_highs_within_tolerance_merge_into_one_zone():
    """Two swing highs very close together should merge into a single BSL zone."""
    candles = flat_series(30, price=1000.0)
    # Place two spike highs close in price (within tolerance)
    candles[5]  = make_candle(candles[5].timestamp,  1000, 1010.05, 1000, 1000)
    candles[15] = make_candle(candles[15].timestamp, 1000, 1010.08, 1000, 1000)

    zones = _build(candles)
    bsl = [z for z in zones if z.zone_type == ZoneType.BSL]
    assert len(bsl) == 1
    assert len(bsl[0].source_pivot_indices) == 2


def test_two_swing_highs_far_apart_create_two_zones():
    """Two swing highs far apart in price should produce two separate BSL zones."""
    candles = flat_series(30, price=1000.0)
    candles[5]  = make_candle(candles[5].timestamp,  1000, 1010.0, 1000, 1000)
    candles[15] = make_candle(candles[15].timestamp, 1000, 1050.0, 1000, 1000)

    zones = _build(candles)
    bsl = [z for z in zones if z.zone_type == ZoneType.BSL]
    assert len(bsl) == 2


def test_bsl_zone_invalidated_when_candle_closes_above_upper():
    """BSL zone is INVALIDATED when a candle closes above zone.upper."""
    candles = with_swing_high(base_price=1000.0, swing_price=1010.0, swing_at=5, n=20)
    upper = 1010.0 + TOLERANCE
    # Use a close-only break — keep H at base_price+1 to avoid creating a new pivot
    candles[12] = make_candle(candles[12].timestamp, 1000, 1001, 999, upper + 0.5)

    zones = _build(candles)
    bsl = [z for z in zones if z.zone_type == ZoneType.BSL]
    # The original zone (source_pivot_indices=[5]) must be INVALIDATED
    original = next(z for z in bsl if 5 in z.source_pivot_indices)
    assert original.status == ZoneStatus.INVALIDATED


def test_ssl_zone_invalidated_when_candle_closes_below_lower():
    """SSL zone is INVALIDATED when a candle closes below zone.lower."""
    candles = with_swing_low(base_price=1000.0, swing_price=990.0, swing_at=5, n=20)
    lower = 990.0 - TOLERANCE
    # Use a close-only break — keep L at base_price-1 to avoid creating a new pivot
    candles[12] = make_candle(candles[12].timestamp, 1000, 1001, 999, lower - 0.5)

    zones = _build(candles)
    ssl = [z for z in zones if z.zone_type == ZoneType.SSL]
    original = next(z for z in ssl if 5 in z.source_pivot_indices)
    assert original.status == ZoneStatus.INVALIDATED


def test_get_zones_known_at_no_look_ahead():
    """Zones should not appear before their created_at_index."""
    candles = with_swing_high(base_price=1000.0, swing_price=1010.0, swing_at=5, n=15)
    pivots = detect_pivots(candles, left=2, right=2)
    zones = build_zones(candles, pivots, TOLERANCE_POINTS, POINT)
    bsl = [z for z in zones if z.zone_type == ZoneType.BSL]
    assert len(bsl) == 1
    created_idx = bsl[0].created_at_index

    before = get_zones_known_at(zones, created_idx - 1)
    assert all(z.created_at_index <= created_idx - 1 for z in before)

    at = get_zones_known_at(zones, created_idx)
    assert any(z.created_at_index == created_idx for z in at)


def test_no_zones_on_flat_series():
    candles = flat_series(20, price=1000.0)
    zones = _build(candles)
    assert zones == []
