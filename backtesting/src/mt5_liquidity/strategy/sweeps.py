"""M15 liquidity sweep detection. No MT5 imports."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from .models import LiquidityZone, SweepEvidence, ZoneStatus, ZoneType

logger = logging.getLogger(__name__)


@dataclass
class SweepSignal:
    zone_id: str
    zone_type: ZoneType
    sweep_candle_index: int   # index in the M15 candle list
    sweep_candle_ts: datetime
    wick_extreme: float       # high (BSL) or low (SSL) that pierced the zone
    close_price: float        # closed back inside the zone boundary
    zone_upper: float
    zone_lower: float
    min_sweep_points: int     # config value that was applied


def detect_sweeps(
    m15_candles: list,
    zones: list[LiquidityZone],
    min_sweep_points: int,
    point: float,
) -> list[SweepSignal]:
    """
    Replay completed M15 candles chronologically.

    For each candle only consider zones created BEFORE the candle's index
    (strict less-than: no same-bar evaluation, no look-ahead).
    Only ACTIVE zones are eligible.

    BSL sweep (SELL setup trigger):
      candle.high > zone.upper + min_sweep_points * point   -- wick pierced by minimum distance
      AND candle.close <= zone.upper                         -- closed back inside

    SSL sweep (BUY setup trigger):
      candle.low < zone.lower - min_sweep_points * point    -- wick pierced by minimum distance
      AND candle.close >= zone.lower                         -- closed back inside

    On sweep:
    - zone.status → SWEPT, zone.sweep_evidence populated
    - SweepSignal appended; that zone is then skipped in all later candles

    Returns SweepSignals in chronological order. One signal per zone maximum.
    """
    min_dist = min_sweep_points * point
    signals: list[SweepSignal] = []

    # Zones pre-swept by build_zones already have status=SWEPT + sweep_evidence.
    # Emit a SweepSignal for each so run_pipeline can process them, then the
    # main replay loop skips them (status != ACTIVE).
    for zone in zones:
        if zone.status == ZoneStatus.SWEPT and zone.sweep_evidence is not None:
            ev = zone.sweep_evidence
            signals.append(SweepSignal(
                zone_id=zone.zone_id,
                zone_type=zone.zone_type,
                sweep_candle_index=ev.candle_index,
                sweep_candle_ts=ev.candle_timestamp,
                wick_extreme=ev.wick_extreme,
                close_price=ev.close_price,
                zone_upper=zone.upper,
                zone_lower=zone.lower,
                min_sweep_points=min_sweep_points,
            ))

    for i, candle in enumerate(m15_candles):
        for zone in zones:
            # No look-ahead: zone must have been created strictly before this candle
            if zone.created_at_index >= i:
                continue
            if zone.status != ZoneStatus.ACTIVE:
                continue

            swept = False

            if zone.zone_type == ZoneType.BSL:
                if candle.high > zone.upper + min_dist and candle.close <= zone.upper:
                    swept = True
                    wick_extreme = candle.high

            elif zone.zone_type == ZoneType.SSL:
                if candle.low < zone.lower - min_dist and candle.close >= zone.lower:
                    swept = True
                    wick_extreme = candle.low

            if swept:
                zone.status = ZoneStatus.SWEPT
                zone.sweep_evidence = SweepEvidence(
                    candle_index=i,
                    candle_timestamp=candle.timestamp,
                    wick_extreme=wick_extreme,
                    close_price=candle.close,
                    zone_id=zone.zone_id,
                )
                zone.add_event(
                    "SWEPT",
                    candle.timestamp,
                    candle_index=i,
                    price=wick_extreme,
                    detail=(
                        f"sweep: wick={wick_extreme}, close={candle.close}, "
                        f"upper={zone.upper}, lower={zone.lower}, "
                        f"min_dist={min_dist}"
                    ),
                )
                signals.append(SweepSignal(
                    zone_id=zone.zone_id,
                    zone_type=zone.zone_type,
                    sweep_candle_index=i,
                    sweep_candle_ts=candle.timestamp,
                    wick_extreme=wick_extreme,
                    close_price=candle.close,
                    zone_upper=zone.upper,
                    zone_lower=zone.lower,
                    min_sweep_points=min_sweep_points,
                ))
                logger.debug(
                    "%s zone %s swept at M15 idx %d", zone.zone_type.value, zone.zone_id, i
                )

    signals.sort(key=lambda s: s.sweep_candle_index)
    return signals
