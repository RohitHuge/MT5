"""Unit tests for liquidity zone construction — Phase 2 acceptance fixes."""
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


# ── Original tests (unchanged behaviour) ─────────────────────────────────────

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
    candles = flat_series(30, price=1000.0)
    candles[5]  = make_candle(candles[5].timestamp,  1000, 1010.05, 1000, 1000)
    candles[15] = make_candle(candles[15].timestamp, 1000, 1010.08, 1000, 1000)
    zones = _build(candles)
    bsl = [z for z in zones if z.zone_type == ZoneType.BSL]
    assert len(bsl) == 1
    assert len(bsl[0].source_pivot_indices) == 2


def test_two_swing_highs_far_apart_create_two_zones():
    candles = flat_series(30, price=1000.0)
    candles[5]  = make_candle(candles[5].timestamp,  1000, 1010.0, 1000, 1000)
    candles[15] = make_candle(candles[15].timestamp, 1000, 1050.0, 1000, 1000)
    zones = _build(candles)
    bsl = [z for z in zones if z.zone_type == ZoneType.BSL]
    assert len(bsl) == 2


def test_bsl_zone_invalidated_when_candle_closes_above_upper():
    candles = with_swing_high(base_price=1000.0, swing_price=1010.0, swing_at=5, n=20)
    upper = 1010.0 + TOLERANCE
    candles[12] = make_candle(candles[12].timestamp, 1000, 1001, 999, upper + 0.5)
    zones = _build(candles)
    bsl = [z for z in zones if z.zone_type == ZoneType.BSL]
    original = next(z for z in bsl if 5 in z.source_pivot_indices)
    assert original.status == ZoneStatus.INVALIDATED


def test_ssl_zone_invalidated_when_candle_closes_below_lower():
    candles = with_swing_low(base_price=1000.0, swing_price=990.0, swing_at=5, n=20)
    lower = 990.0 - TOLERANCE
    candles[12] = make_candle(candles[12].timestamp, 1000, 1001, 999, lower - 0.5)
    zones = _build(candles)
    ssl = [z for z in zones if z.zone_type == ZoneType.SSL]
    original = next(z for z in ssl if 5 in z.source_pivot_indices)
    assert original.status == ZoneStatus.INVALIDATED


def test_get_zones_known_at_no_look_ahead():
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


# ── Task 2: Sweep vs Invalidation ────────────────────────────────────────────

def test_bsl_zone_swept_when_wick_above_and_close_inside():
    """BSL: wick above upper + close inside → SWEPT with sweep_evidence."""
    candles = with_swing_high(base_price=1000.0, swing_price=1010.0, swing_at=5, n=20)
    upper = 1010.0 + TOLERANCE   # 1010.10
    # Wick pierces above upper, but close stays below upper
    candles[12] = make_candle(candles[12].timestamp, 1000, upper + 1.0, 999, upper - 0.5)
    zones = _build(candles)
    bsl = [z for z in zones if z.zone_type == ZoneType.BSL]
    original = next(z for z in bsl if 5 in z.source_pivot_indices)
    assert original.status == ZoneStatus.SWEPT
    assert original.sweep_evidence is not None
    se = original.sweep_evidence
    assert se.wick_extreme > upper
    assert se.close_price <= upper
    assert se.candle_index == 12


def test_bsl_zone_invalidated_no_sweep_evidence():
    """BSL: close above upper → INVALIDATED, sweep_evidence must be None."""
    candles = with_swing_high(base_price=1000.0, swing_price=1010.0, swing_at=5, n=20)
    upper = 1010.0 + TOLERANCE
    candles[12] = make_candle(candles[12].timestamp, 1000, 1001, 999, upper + 0.5)
    zones = _build(candles)
    bsl = [z for z in zones if z.zone_type == ZoneType.BSL]
    original = next(z for z in bsl if 5 in z.source_pivot_indices)
    assert original.status == ZoneStatus.INVALIDATED
    assert original.sweep_evidence is None


def test_ssl_zone_swept_when_wick_below_and_close_inside():
    """SSL: wick below lower + close inside → SWEPT with sweep_evidence."""
    candles = with_swing_low(base_price=1000.0, swing_price=990.0, swing_at=5, n=20)
    lower = 990.0 - TOLERANCE   # 989.90
    # Wick below lower, close back above lower
    candles[12] = make_candle(candles[12].timestamp, 1000, 1001, lower - 1.0, lower + 0.5)
    zones = _build(candles)
    ssl = [z for z in zones if z.zone_type == ZoneType.SSL]
    original = next(z for z in ssl if 5 in z.source_pivot_indices)
    assert original.status == ZoneStatus.SWEPT
    assert original.sweep_evidence is not None
    se = original.sweep_evidence
    assert se.wick_extreme < lower
    assert se.close_price >= lower


def test_ssl_zone_invalidated_no_sweep_evidence():
    """SSL: close below lower → INVALIDATED, sweep_evidence must be None."""
    candles = with_swing_low(base_price=1000.0, swing_price=990.0, swing_at=5, n=20)
    lower = 990.0 - TOLERANCE
    candles[12] = make_candle(candles[12].timestamp, 1000, 1001, 999, lower - 0.5)
    zones = _build(candles)
    ssl = [z for z in zones if z.zone_type == ZoneType.SSL]
    original = next(z for z in ssl if 5 in z.source_pivot_indices)
    assert original.status == ZoneStatus.INVALIDATED
    assert original.sweep_evidence is None


def test_zone_stays_active_when_price_does_not_reach_boundary():
    """Zone remains ACTIVE if price never touches the boundary."""
    candles = with_swing_high(base_price=1000.0, swing_price=1010.0, swing_at=5, n=15)
    # All subsequent candles stay at 1000 — well below zone lower (1009.90)
    zones = _build(candles)
    bsl = [z for z in zones if z.zone_type == ZoneType.BSL]
    assert len(bsl) == 1
    assert bsl[0].status == ZoneStatus.ACTIVE


def test_swept_zone_not_invalidated_by_later_candle():
    """Once SWEPT, a zone must not transition to INVALIDATED by a subsequent candle."""
    candles = with_swing_high(base_price=1000.0, swing_price=1010.0, swing_at=5, n=25)
    upper = 1010.0 + TOLERANCE
    # Candle 12: valid sweep (wick above, close inside)
    candles[12] = make_candle(candles[12].timestamp, 1000, upper + 1.0, 999, upper - 0.5)
    # Candle 15: would invalidate if zone were still ACTIVE
    candles[15] = make_candle(candles[15].timestamp, 1000, 1001, 999, upper + 2.0)
    zones = _build(candles)
    bsl = [z for z in zones if z.zone_type == ZoneType.BSL]
    original = next(z for z in bsl if 5 in z.source_pivot_indices)
    assert original.status == ZoneStatus.SWEPT   # must stay SWEPT, not transition to INVALIDATED


# ── Task 3: Transitive clustering ────────────────────────────────────────────

def test_transitive_cluster_three_pivots_one_zone():
    """
    Three pivots A < B < C where A-B <= tol and B-C <= tol but A-C > tol.
    All three must merge into ONE zone (transitive linkage).
    tolerance = 0.10; A=1010.00, B=1010.08, C=1010.16
    A-B=0.08 <= 0.10 ✓, B-C=0.08 <= 0.10 ✓, A-C=0.16 > 0.10
    """
    candles = flat_series(40, price=1000.0)
    candles[5]  = make_candle(candles[5].timestamp,  1000, 1010.00, 1000, 1000)
    candles[15] = make_candle(candles[15].timestamp, 1000, 1010.08, 1000, 1000)
    candles[25] = make_candle(candles[25].timestamp, 1000, 1010.16, 1000, 1000)
    zones = _build(candles)
    bsl = [z for z in zones if z.zone_type == ZoneType.BSL]
    assert len(bsl) == 1, f"Expected 1 zone, got {len(bsl)}: {[(z.upper, z.lower) for z in bsl]}"
    assert len(bsl[0].source_pivot_indices) == 3


def test_new_pivot_within_zone_expands_not_duplicates():
    """A new pivot whose price falls inside an existing zone must expand it, not create a new zone."""
    candles = flat_series(30, price=1000.0)
    candles[5]  = make_candle(candles[5].timestamp,  1000, 1010.0, 1000, 1000)
    # Second pivot strictly inside the first zone's range: 1010.0 ± 0.10 → [1009.90, 1010.10]
    # Pivot at 1010.05 is inside [1009.90, 1010.10]
    candles[15] = make_candle(candles[15].timestamp, 1000, 1010.05, 1000, 1000)
    zones = _build(candles)
    bsl = [z for z in zones if z.zone_type == ZoneType.BSL]
    assert len(bsl) == 1
    assert len(bsl[0].source_pivot_indices) == 2


def test_repeated_identical_pivot_price_one_zone():
    """Two pivots at exactly the same price must produce exactly one zone."""
    candles = flat_series(30, price=1000.0)
    candles[5]  = make_candle(candles[5].timestamp,  1000, 1010.0, 1000, 1000)
    candles[15] = make_candle(candles[15].timestamp, 1000, 1010.0, 1000, 1000)
    zones = _build(candles)
    bsl = [z for z in zones if z.zone_type == ZoneType.BSL]
    assert len(bsl) == 1


# ── Task 4: Zone provenance ───────────────────────────────────────────────────

def test_zone_has_source_pivots_populated():
    """Each zone must have source_pivots list matching source_pivot_indices."""
    candles = with_swing_high(base_price=1000.0, swing_price=1010.0, swing_at=5, n=15)
    zones = _build(candles)
    bsl = [z for z in zones if z.zone_type == ZoneType.BSL]
    z = bsl[0]
    assert len(z.source_pivots) == len(z.source_pivot_indices)
    assert z.source_pivots[0].price == 1010.0
    assert z.source_pivots[0].pivot_type.value == "HIGH"


def test_zone_events_have_required_fields():
    """Every event dict must have type, ts, candle_index, detail, price."""
    candles = with_swing_high(base_price=1000.0, swing_price=1010.0, swing_at=5, n=20)
    upper = 1010.0 + TOLERANCE
    candles[12] = make_candle(candles[12].timestamp, 1000, 1001, 999, upper + 0.5)
    zones = _build(candles)
    bsl = [z for z in zones if z.zone_type == ZoneType.BSL]
    z = bsl[0]
    for ev in z.events:
        assert "type" in ev
        assert "ts" in ev
        assert "candle_index" in ev
        assert "detail" in ev
        assert "price" in ev


def test_invalidation_event_recorded_with_reason():
    """INVALIDATED event must include the close price and boundary in its detail."""
    candles = with_swing_high(base_price=1000.0, swing_price=1010.0, swing_at=5, n=20)
    upper = 1010.0 + TOLERANCE
    candles[12] = make_candle(candles[12].timestamp, 1000, 1001, 999, upper + 0.5)
    zones = _build(candles)
    bsl = [z for z in zones if z.zone_type == ZoneType.BSL]
    z = next(zz for zz in bsl if 5 in zz.source_pivot_indices)
    inv_events = [e for e in z.events if e["type"] == "INVALIDATED"]
    assert len(inv_events) == 1
    assert "close" in inv_events[0]["detail"].lower() or "upper" in inv_events[0]["detail"].lower()
