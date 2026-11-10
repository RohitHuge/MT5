"""Top-level Phase 3 entry point. Builds zones and pivots, then runs the pipeline."""
from __future__ import annotations

from ..data.candles import Candle
from .liquidity_zones import build_zones
from .setup import Setup, run_pipeline
from .swings import detect_pivots


def run_liquidity_sweep_strategy(
    m15_candles: list[Candle],
    m5_candles: list[Candle],
    config,
    point: float,
) -> list[Setup]:
    """
    Full Phase 3 strategy run from raw candle data.

    1. Detect M5 pivots (used for MSS detection).
    2. Detect M15 pivots and build BSL/SSL zones from M15 candles.
    3. Run the sweep → MSS → retest → entry pipeline.

    Returns all Setups (ENTRY_READY, EXPIRED, MISSED) for auditability.
    No MT5 API calls. No order execution.
    """
    m5_pivots = detect_pivots(m5_candles, config.pivot_left, config.pivot_right)

    m15_pivots = detect_pivots(m15_candles, config.pivot_left, config.pivot_right)
    zones = build_zones(
        candles=m15_candles,
        pivots=m15_pivots,
        tolerance_points=config.zone_tolerance_points,
        point=point,
    )

    return run_pipeline(
        m15_candles=m15_candles,
        m5_candles=m5_candles,
        m5_pivots=m5_pivots,
        zones=zones,
        config=config,
        point=point,
    )
