"""Unit tests for pivot detection."""
from __future__ import annotations

import pytest

from mt5_liquidity.strategy.models import PivotType
from mt5_liquidity.strategy.swings import detect_pivots, get_pivots_known_at
from tests.fixtures.candle_fixtures import (
    with_swing_high,
    with_swing_low,
    flat_series,
)


def test_swing_high_confirmed_at_index_plus_right():
    """Pivot HIGH at index 5, left=2, right=2 → confirmed at index 7."""
    candles = with_swing_high(base_price=1000.0, swing_price=1010.0, swing_at=5, n=15)
    pivots = detect_pivots(candles, left=2, right=2)
    highs = [p for p in pivots if p.pivot_type == PivotType.HIGH]
    assert len(highs) == 1
    p = highs[0]
    assert p.index == 5
    assert p.confirmed_at_index == 7
    assert p.price == 1010.0


def test_swing_low_confirmed_at_index_plus_right():
    """Pivot LOW at index 5, left=2, right=2 → confirmed at index 7."""
    candles = with_swing_low(base_price=1000.0, swing_price=990.0, swing_at=5, n=15)
    pivots = detect_pivots(candles, left=2, right=2)
    lows = [p for p in pivots if p.pivot_type == PivotType.LOW]
    assert len(lows) == 1
    p = lows[0]
    assert p.index == 5
    assert p.confirmed_at_index == 7
    assert p.price == 990.0


def test_pivot_not_returned_when_insufficient_right_bars():
    """Pivot at index 12, right=2: needs index 14. With only 14 candles (0-13), no pivot."""
    candles = with_swing_high(base_price=1000.0, swing_price=1010.0, swing_at=12, n=14)
    pivots = detect_pivots(candles, left=2, right=2)
    highs = [p for p in pivots if p.pivot_type == PivotType.HIGH]
    assert len(highs) == 0


def test_equal_high_on_right_disqualifies_pivot():
    """Equal high to the right means strict inequality fails → no pivot."""
    candles = with_swing_high(base_price=1000.0, swing_price=1010.0, swing_at=5, n=15)
    # Make right neighbour equal the swing high
    candles[6] = candles[6].__class__(
        timestamp=candles[6].timestamp,
        open=1000.0,
        high=1010.0,  # equal high
        low=1000.0,
        close=1000.0,
    )
    pivots = detect_pivots(candles, left=2, right=2)
    highs = [p for p in pivots if p.pivot_type == PivotType.HIGH]
    assert len(highs) == 0


def test_equal_high_on_left_disqualifies_pivot():
    """Equal high to the left means strict inequality fails → no pivot."""
    candles = with_swing_high(base_price=1000.0, swing_price=1010.0, swing_at=5, n=15)
    candles[4] = candles[4].__class__(
        timestamp=candles[4].timestamp,
        open=1000.0,
        high=1010.0,  # equal high on left
        low=1000.0,
        close=1000.0,
    )
    pivots = detect_pivots(candles, left=2, right=2)
    highs = [p for p in pivots if p.pivot_type == PivotType.HIGH]
    assert len(highs) == 0


def test_insufficient_candles_returns_empty():
    candles = flat_series(3, price=1000.0)  # fewer than left+1+right=5
    pivots = detect_pivots(candles, left=2, right=2)
    assert pivots == []


def test_get_pivots_known_at_filters_correctly():
    candles = with_swing_high(base_price=1000.0, swing_price=1010.0, swing_at=5, n=15)
    pivots = detect_pivots(candles, left=2, right=2)
    highs = [p for p in pivots if p.pivot_type == PivotType.HIGH]
    assert len(highs) == 1
    confirmed_idx = highs[0].confirmed_at_index  # should be 7

    # Before confirmation → not visible
    before = get_pivots_known_at(pivots, confirmed_idx - 1)
    assert all(p.confirmed_at_index <= confirmed_idx - 1 for p in before)

    # At confirmation → visible
    at = get_pivots_known_at(pivots, confirmed_idx)
    assert any(p.confirmed_at_index == confirmed_idx for p in at)


def test_no_pivots_on_flat_series():
    candles = flat_series(20, price=1000.0)
    pivots = detect_pivots(candles, left=2, right=2)
    assert pivots == []
