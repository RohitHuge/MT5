"""M5 Market-Structure Shift (MSS) detection after a confirmed M15 sweep."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from .models import Pivot, PivotType, ZoneType
from .sweeps import SweepSignal

logger = logging.getLogger(__name__)


@dataclass
class MSSSignal:
    sweep_zone_id: str
    direction: str               # "BEARISH" or "BULLISH"
    broken_pivot_index: int      # pivot's own candle index
    broken_pivot_price: float
    broken_pivot_confirmed_at: int   # confirmed_at_index of the broken pivot
    break_candle_index: int      # M5 index where MSS was confirmed
    break_candle_ts: datetime
    break_candle_close: float
    bars_after_sweep: int        # M5 bars elapsed from sweep_m5_index to break_candle_index


def detect_mss(
    m5_candles: list,
    m5_pivots: list[Pivot],
    sweep: SweepSignal,
    sweep_m5_index: int,
    mss_timeout_bars: int,
    point: float,
) -> MSSSignal | None:
    """
    Detect the first M5 Market-Structure Shift after a confirmed M15 sweep.

    BSL sweep → look for BEARISH MSS:
      A completed M5 candle closes BELOW a previously confirmed M5 swing LOW.

    SSL sweep → look for BULLISH MSS:
      A completed M5 candle closes ABOVE a previously confirmed M5 swing HIGH.

    Constraints:
    - Only inspect M5 candles from sweep_m5_index onward (inclusive).
    - Stop at sweep_m5_index + mss_timeout_bars - 1 (inclusive).
    - The broken pivot must have confirmed_at_index STRICTLY LESS THAN the break candle
      index (confirmed before the break — no look-ahead).
    - Returns the first qualifying MSS, or None if none found within timeout.
    """
    is_bearish = sweep.zone_type == ZoneType.BSL

    end_index = min(sweep_m5_index + mss_timeout_bars, len(m5_candles))

    for i in range(sweep_m5_index, end_index):
        candle = m5_candles[i]
        bars_after = i - sweep_m5_index

        # Collect pivots confirmed strictly before this candle
        known_pivots = [p for p in m5_pivots if p.confirmed_at_index < i]

        if is_bearish:
            # Need a confirmed swing LOW that this candle closes below
            low_pivots = [p for p in known_pivots if p.pivot_type == PivotType.LOW]
            for pivot in sorted(low_pivots, key=lambda p: p.price, reverse=True):
                if candle.close < pivot.price:
                    logger.debug(
                        "Bearish MSS at M5 idx %d, closed %.5f below pivot %.5f (confirmed at %d)",
                        i, candle.close, pivot.price, pivot.confirmed_at_index,
                    )
                    return MSSSignal(
                        sweep_zone_id=sweep.zone_id,
                        direction="BEARISH",
                        broken_pivot_index=pivot.index,
                        broken_pivot_price=pivot.price,
                        broken_pivot_confirmed_at=pivot.confirmed_at_index,
                        break_candle_index=i,
                        break_candle_ts=candle.timestamp,
                        break_candle_close=candle.close,
                        bars_after_sweep=bars_after,
                    )

        else:
            # Need a confirmed swing HIGH that this candle closes above
            high_pivots = [p for p in known_pivots if p.pivot_type == PivotType.HIGH]
            for pivot in sorted(high_pivots, key=lambda p: p.price):
                if candle.close > pivot.price:
                    logger.debug(
                        "Bullish MSS at M5 idx %d, closed %.5f above pivot %.5f (confirmed at %d)",
                        i, candle.close, pivot.price, pivot.confirmed_at_index,
                    )
                    return MSSSignal(
                        sweep_zone_id=sweep.zone_id,
                        direction="BULLISH",
                        broken_pivot_index=pivot.index,
                        broken_pivot_price=pivot.price,
                        broken_pivot_confirmed_at=pivot.confirmed_at_index,
                        break_candle_index=i,
                        break_candle_ts=candle.timestamp,
                        break_candle_close=candle.close,
                        bars_after_sweep=bars_after,
                    )

    logger.debug("MSS timeout: no MSS in %d bars after M5 idx %d", mss_timeout_bars, sweep_m5_index)
    return None
