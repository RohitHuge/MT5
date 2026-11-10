"""Retest and rejection-candle detection after a confirmed M5 MSS."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from .market_structure import MSSSignal

logger = logging.getLogger(__name__)


@dataclass
class RetestSignal:
    mss_zone_id: str
    direction: str               # "BEARISH" or "BULLISH"
    structure_level: float       # broken swing price (the MSS level)
    retest_band_upper: float
    retest_band_lower: float
    rejection_candle_index: int
    rejection_candle_ts: datetime
    rejection_candle_close: float
    rejection_candle_open: float
    signal_price: float          # rejection_candle.close — Phase 4 will determine actual fill
    bars_after_mss: int


def detect_retest(
    m5_candles: list,
    mss: MSSSignal,
    mss_candle_index: int,
    retest_band_points: int,
    retest_timeout_bars: int,
    point: float,
) -> RetestSignal | None:
    """
    Detect the first valid retest + rejection after an M5 MSS.

    Structure level = mss.broken_pivot_price (the broken swing).

    Bearish retest (after bearish MSS):
      Price trades UP into the retest band from below:
        candle.high >= retest_band_lower
      Rejection: candle closes BELOW the structure level:
        candle.close < structure_level

    Bullish retest (after bullish MSS):
      Price trades DOWN into the retest band from above:
        candle.low <= retest_band_upper
      Rejection: candle closes ABOVE the structure level:
        candle.close > structure_level

    Retest band:
      upper = structure_level + retest_band_points * point
      lower = structure_level - retest_band_points * point

    Search from mss_candle_index + 1 (exclusive — MSS candle itself is not a retest)
    up to mss_candle_index + retest_timeout_bars (inclusive).
    Entry price = rejection_candle.close.
    Returns None if no valid rejection found within timeout.
    """
    level = mss.broken_pivot_price
    band = retest_band_points * point
    band_upper = level + band
    band_lower = level - band

    start = mss_candle_index + 1
    end = min(start + retest_timeout_bars, len(m5_candles))

    for i in range(start, end):
        candle = m5_candles[i]
        bars_after = i - mss_candle_index

        if mss.direction == "BEARISH":
            touched_band = candle.high >= band_lower
            rejected = candle.close < level
            if touched_band and rejected:
                logger.debug(
                    "Bearish retest at M5 idx %d: high=%.5f >= band_lower=%.5f, close=%.5f < level=%.5f",
                    i, candle.high, band_lower, candle.close, level,
                )
                return RetestSignal(
                    mss_zone_id=mss.sweep_zone_id,
                    direction="BEARISH",
                    structure_level=level,
                    retest_band_upper=band_upper,
                    retest_band_lower=band_lower,
                    rejection_candle_index=i,
                    rejection_candle_ts=candle.timestamp,
                    rejection_candle_close=candle.close,
                    rejection_candle_open=candle.open,
                    signal_price=candle.close,
                    bars_after_mss=bars_after,
                )

        else:  # BULLISH
            touched_band = candle.low <= band_upper
            rejected = candle.close > level
            if touched_band and rejected:
                logger.debug(
                    "Bullish retest at M5 idx %d: low=%.5f <= band_upper=%.5f, close=%.5f > level=%.5f",
                    i, candle.low, band_upper, candle.close, level,
                )
                return RetestSignal(
                    mss_zone_id=mss.sweep_zone_id,
                    direction="BULLISH",
                    structure_level=level,
                    retest_band_upper=band_upper,
                    retest_band_lower=band_lower,
                    rejection_candle_index=i,
                    rejection_candle_ts=candle.timestamp,
                    rejection_candle_close=candle.close,
                    rejection_candle_open=candle.open,
                    signal_price=candle.close,
                    bars_after_mss=bars_after,
                )

    logger.debug(
        "Retest timeout: no rejection in %d bars after MSS at M5 idx %d",
        retest_timeout_bars, mss_candle_index,
    )
    return None
