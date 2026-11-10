"""Build and maintain BSL/SSL liquidity zones from confirmed pivots."""
from __future__ import annotations

import logging
from datetime import datetime

from ..data.candles import Candle
from .models import LiquidityZone, Pivot, PivotType, SweepEvidence, ZoneStatus, ZoneType
from .swings import get_pivots_known_at

logger = logging.getLogger(__name__)


def _zone_id(zone_type: ZoneType, created_at_index: int) -> str:
    return f"{zone_type.value}_{created_at_index:06d}"


# ── Union-Find for transitive clustering ─────────────────────────────────────

class _UnionFind:
    def __init__(self) -> None:
        self._parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self._parent.setdefault(x, x)
        if self._parent[x] != x:
            self._parent[x] = self.find(self._parent[x])
        return self._parent[x]

    def union(self, x: int, y: int) -> None:
        self._parent[self.find(x)] = self.find(y)

    def groups(self, items: list[int]) -> dict[int, list[int]]:
        result: dict[int, list[int]] = {}
        for i in items:
            root = self.find(i)
            result.setdefault(root, []).append(i)
        return result


def _cluster_pivots(pivots: list[Pivot], tolerance: float) -> list[list[Pivot]]:
    """
    Group pivots into transitive clusters using single-linkage (union-find).
    Two pivots merge if |p1.price - p2.price| <= tolerance.
    A, B, C merge if A-B <= tol and B-C <= tol, even if A-C > tol.
    """
    uf = _UnionFind()
    indices = list(range(len(pivots)))
    for i in indices:
        uf.find(i)  # register
    for i in range(len(pivots)):
        for j in range(i + 1, len(pivots)):
            if abs(pivots[i].price - pivots[j].price) <= tolerance:
                uf.union(i, j)
    groups = uf.groups(indices)
    return [[pivots[k] for k in sorted(members)] for members in groups.values()]


def build_zones(
    candles: list[Candle],
    pivots: list[Pivot],
    tolerance_points: float,
    point: float = 0.01,
) -> list[LiquidityZone]:
    """
    Build BSL and SSL zones candle-by-candle so zones only appear when their
    source pivots are known. No look-ahead.

    BSL = zone around confirmed swing HIGHs (buy-side liquidity).
    SSL = zone around confirmed swing LOWs (sell-side liquidity).

    Clustering (transitive, Task 3):
    - Pivots within tolerance of each other (directly or transitively) merge into one zone.
    - A new pivot whose price falls within an existing active zone's boundaries expands that zone.
    - Prevents duplicate zones from the same cluster.

    Zone lifecycle (Phase 2):
    - ACTIVE: zone is valid, no interaction yet.
    - SWEPT: a candle's wick pierced the zone boundary AND closed back inside.
              SweepEvidence is stored. Once SWEPT, cannot become INVALIDATED.
    - INVALIDATED: a candle CLOSED beyond the zone boundary (no close-back).
    - (CONSUMED and EXPIRED are Phase 3+ concerns.)

    BSL sweep:  candle.high > zone.upper  AND candle.close <= zone.upper
    BSL breakout: candle.close > zone.upper  (no close-back)
    SSL sweep:  candle.low < zone.lower   AND candle.close >= zone.lower
    SSL breakout: candle.close < zone.lower

    Task 4 — Provenance:
    - Each zone stores source_pivots (full Pivot objects).
    - Every status transition calls add_event with type, ts, candle_index, price, detail.
    """
    tolerance = tolerance_points * point
    zones: list[LiquidityZone] = []

    # Precompute transitive clusters, then activate each pivot only when its
    # confirmation candle is reached. This preserves causal/no-look-ahead use.
    by_confirmation: dict[int, list[list[Pivot]]] = {}
    for pivot_type in (PivotType.HIGH, PivotType.LOW):
        typed = [p for p in pivots if p.pivot_type == pivot_type]
        for cluster in _cluster_pivots(typed, tolerance):
            confirmation_index = max(p.confirmed_at_index for p in cluster)
            by_confirmation.setdefault(confirmation_index, []).append(cluster)

    for i, candle in enumerate(candles):
        # 1. Discover clusters only when every member pivot is confirmed.
        for cluster in by_confirmation.get(i, []):
            pivot_type = cluster[0].pivot_type
            zone_type = ZoneType.BSL if pivot_type == PivotType.HIGH else ZoneType.SSL
            cluster_pivots = sorted(cluster, key=lambda p: p.confirmed_at_index)
            # A connected cluster may span more than a single tolerance band.
            upper = max(p.price for p in cluster_pivots) + tolerance
            lower = min(p.price for p in cluster_pivots) - tolerance
            first = cluster_pivots[0]
            zone = LiquidityZone(
                zone_id=_zone_id(zone_type, i),
                zone_type=zone_type,
                upper=upper,
                lower=lower,
                source_pivot_indices=[p.index for p in cluster_pivots],
                source_pivots=list(cluster_pivots),
                created_at_index=i,
                created_at=candle.timestamp,
            )
            zone.add_event(
                "CREATED", candle.timestamp, candle_index=i, price=first.price,
                detail=(
                    f"Transitive cluster with {len(cluster_pivots)} pivots; "
                    f"all confirmed by index {i}"
                ),
            )
            zones.append(zone)

        # 2. Update active zone statuses based on current candle
        for zone in zones:
            if zone.status not in (ZoneStatus.ACTIVE,):
                continue  # SWEPT, INVALIDATED, etc. are terminal in Phase 2
            if zone.created_at_index >= i:
                continue  # zone created this same candle — skip same-bar evaluation

            if zone.zone_type == ZoneType.BSL:
                if candle.high > zone.upper and candle.close <= zone.upper:
                    # Valid sweep: wick above, close back inside
                    zone.status = ZoneStatus.SWEPT
                    zone.sweep_evidence = SweepEvidence(
                        candle_index=i,
                        candle_timestamp=candle.timestamp,
                        wick_extreme=candle.high,
                        close_price=candle.close,
                        zone_id=zone.zone_id,
                    )
                    zone.add_event(
                        "SWEPT", candle.timestamp, candle_index=i,
                        price=candle.high,
                        detail=f"wick {candle.high} > upper {zone.upper}, close {candle.close} inside",
                    )
                    logger.debug("BSL zone %s SWEPT at %s", zone.zone_id, candle.timestamp)

                elif candle.close > zone.upper:
                    # Breakout / invalidation
                    zone.status = ZoneStatus.INVALIDATED
                    zone.invalidated_at = candle.timestamp
                    zone.add_event(
                        "INVALIDATED", candle.timestamp, candle_index=i,
                        price=candle.close,
                        detail=f"close {candle.close} > upper {zone.upper}",
                    )
                    logger.debug("BSL zone %s INVALIDATED at %s", zone.zone_id, candle.timestamp)

            elif zone.zone_type == ZoneType.SSL:
                if candle.low < zone.lower and candle.close >= zone.lower:
                    # Valid sweep: wick below, close back inside
                    zone.status = ZoneStatus.SWEPT
                    zone.sweep_evidence = SweepEvidence(
                        candle_index=i,
                        candle_timestamp=candle.timestamp,
                        wick_extreme=candle.low,
                        close_price=candle.close,
                        zone_id=zone.zone_id,
                    )
                    zone.add_event(
                        "SWEPT", candle.timestamp, candle_index=i,
                        price=candle.low,
                        detail=f"wick {candle.low} < lower {zone.lower}, close {candle.close} inside",
                    )
                    logger.debug("SSL zone %s SWEPT at %s", zone.zone_id, candle.timestamp)

                elif candle.close < zone.lower:
                    # Breakout / invalidation
                    zone.status = ZoneStatus.INVALIDATED
                    zone.invalidated_at = candle.timestamp
                    zone.add_event(
                        "INVALIDATED", candle.timestamp, candle_index=i,
                        price=candle.close,
                        detail=f"close {candle.close} < lower {zone.lower}",
                    )
                    logger.debug("SSL zone %s INVALIDATED at %s", zone.zone_id, candle.timestamp)

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
    """
    Add pivot to an existing active zone cluster or create a new zone.

    Task 3 — no-duplicate rule:
    If pivot.price falls within an existing ACTIVE zone's [lower-tolerance, upper+tolerance]
    band, expand that zone rather than creating a new one.
    """
    for zone in zones:
        if zone.zone_type != zone_type or zone.status != ZoneStatus.ACTIVE:
            continue
        if zone.lower - tolerance <= pivot.price <= zone.upper + tolerance:
            # Expand zone to absorb this pivot
            new_upper = max(zone.upper, pivot.price + tolerance)
            new_lower = min(zone.lower, pivot.price - tolerance)
            zone.upper = new_upper
            zone.lower = new_lower
            zone.source_pivot_indices.append(pivot.index)
            zone.source_pivots.append(pivot)
            zone.add_event(
                "EXPANDED", created_at, candle_index=created_at_index,
                price=pivot.price,
                detail=f"Pivot idx={pivot.index} price={pivot.price} confirmed_at={pivot.confirmed_at.isoformat()}",
            )
            return

    # No existing zone absorbed this pivot — create a new one
    new_zone = LiquidityZone(
        zone_id=_zone_id(zone_type, created_at_index),
        zone_type=zone_type,
        upper=pivot.price + tolerance,
        lower=pivot.price - tolerance,
        source_pivot_indices=[pivot.index],
        source_pivots=[pivot],
        created_at_index=created_at_index,
        created_at=created_at,
    )
    new_zone.add_event(
        "CREATED", created_at, candle_index=created_at_index,
        price=pivot.price,
        detail=f"Pivot idx={pivot.index} price={pivot.price} confirmed_at={pivot.confirmed_at.isoformat()}",
    )
    zones.append(new_zone)


def get_zones_known_at(zones: list[LiquidityZone], at_index: int) -> list[LiquidityZone]:
    """Return only zones created at or before at_index. No look-ahead."""
    return [z for z in zones if z.created_at_index <= at_index]
