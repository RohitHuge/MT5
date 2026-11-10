"""Build and maintain BSL/SSL liquidity zones from confirmed pivots."""
from __future__ import annotations

import logging
from datetime import datetime

from ..data.candles import Candle
from .models import LiquidityZone, PivotType, ZoneStatus, ZoneType
from .swings import Pivot, get_pivots_known_at

logger = logging.getLogger(__name__)


def _zone_id(zone_type: ZoneType, created_at_index: int) -> str:
    return f"{zone_type.value}_{created_at_index:06d}"


def _pivots_within_tolerance(
    p_price: float,
    zone: LiquidityZone,
    tolerance: float,
) -> bool:
    """True if p_price is within tolerance of the zone's existing cluster."""
    return abs(p_price - zone.lower + tolerance) <= 2 * tolerance or (
        zone.lower - tolerance <= p_price <= zone.upper + tolerance
    )


def build_zones(
    candles: list[Candle],
    pivots: list[Pivot],
    tolerance_points: float,
    point: float = 0.01,
) -> list[LiquidityZone]:
    """
    Build BSL and SSL zones candle-by-candle so zones only appear when their
    source pivots are known. No look-ahead.

    BSL = zone around confirmed swing HIGHs.
    SSL = zone around confirmed swing LOWs.

    Clustering: pivot highs/lows within tolerance_points*point of each other
    merge into one zone.  A new pivot expanding a cluster updates the boundaries.

    Invalidation (Phase 2 only):
      BSL: candle CLOSES above zone.upper  → INVALIDATED
      SSL: candle CLOSES below zone.lower  → INVALIDATED
    """
    tolerance = tolerance_points * point
    zones: list[LiquidityZone] = []

    for i, candle in enumerate(candles):
        # 1. Discover any newly confirmed pivots at this index
        newly_confirmed = [p for p in pivots if p.confirmed_at_index == i]

        for pivot in newly_confirmed:
            if pivot.pivot_type == PivotType.HIGH:
                _integrate_pivot(zones, pivot, ZoneType.BSL, tolerance, candle.timestamp, i)
            else:
                _integrate_pivot(zones, pivot, ZoneType.SSL, tolerance, candle.timestamp, i)

        # 2. Update active zone statuses based on current candle close
        for zone in zones:
            if zone.status != ZoneStatus.ACTIVE:
                continue
            if zone.created_at_index >= i:
                continue  # zone created this candle; skip invalidation check same bar

            if zone.zone_type == ZoneType.BSL and candle.close > zone.upper:
                zone.status = ZoneStatus.INVALIDATED
                zone.invalidated_at = candle.timestamp
                zone.add_event("INVALIDATED", candle.timestamp,
                               f"Close {candle.close} > upper {zone.upper}")
                logger.debug("BSL zone %s invalidated at %s", zone.zone_id, candle.timestamp)

            elif zone.zone_type == ZoneType.SSL and candle.close < zone.lower:
                zone.status = ZoneStatus.INVALIDATED
                zone.invalidated_at = candle.timestamp
                zone.add_event("INVALIDATED", candle.timestamp,
                               f"Close {candle.close} < lower {zone.lower}")
                logger.debug("SSL zone %s invalidated at %s", zone.zone_id, candle.timestamp)

    zones.sort(key=lambda z: z.created_at_index)
    return zones


def _integrate_pivot(
    zones: list[LiquidityZone],
    pivot: Pivot,
    zone_type: ZoneType,
    tolerance: float,
    created_at: datetime,
    created_at_index: int,
) -> None:
    """Add pivot to an existing active zone cluster or create a new zone."""
    # Look for an existing ACTIVE zone of the same type to merge into
    for zone in zones:
        if zone.zone_type != zone_type or zone.status != ZoneStatus.ACTIVE:
            continue
        # Check if pivot price falls within (or adjacent to) the zone's tolerance band
        if zone.lower - tolerance <= pivot.price <= zone.upper + tolerance:
            # Expand zone boundaries to include this pivot
            new_upper = max(zone.upper, pivot.price + tolerance)
            new_lower = min(zone.lower, pivot.price - tolerance)
            zone.upper = new_upper
            zone.lower = new_lower
            zone.source_pivot_indices.append(pivot.index)
            zone.add_event("EXPANDED", created_at,
                           f"Pivot idx={pivot.index} price={pivot.price}")
            return

    # No existing zone absorbed this pivot — create a new one
    new_zone = LiquidityZone(
        zone_id=_zone_id(zone_type, created_at_index),
        zone_type=zone_type,
        upper=pivot.price + tolerance,
        lower=pivot.price - tolerance,
        source_pivot_indices=[pivot.index],
        created_at_index=created_at_index,
        created_at=created_at,
    )
    new_zone.add_event("CREATED", created_at, f"Pivot idx={pivot.index} price={pivot.price}")
    zones.append(new_zone)


def get_zones_known_at(zones: list[LiquidityZone], at_index: int) -> list[LiquidityZone]:
    """Return only zones created at or before at_index. No look-ahead."""
    return [z for z in zones if z.created_at_index <= at_index]
