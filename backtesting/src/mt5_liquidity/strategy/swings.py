"""Confirmed pivot high/low detection with strict no-look-ahead guarantee."""
from __future__ import annotations

from ..data.candles import Candle
from .models import Pivot, PivotType


def detect_pivots(candles: list[Candle], left: int, right: int) -> list[Pivot]:
    """
    Detect confirmed pivot highs and lows.

    Pivot HIGH at index i:
      - candles[i-left:i] all have high STRICTLY < candles[i].high
      - candles[i+1:i+right+1] all have high STRICTLY < candles[i].high
      - Equal highs on either side disqualify (strict inequality, Version 1)
      - confirmed_at_index = i + right

    Pivot LOW at index i:
      - candles[i-left:i] all have low STRICTLY > candles[i].low
      - candles[i+1:i+right+1] all have low STRICTLY > candles[i].low
      - confirmed_at_index = i + right

    Only indices where i >= left and i + right < len(candles) are evaluated.
    Returns list sorted by confirmed_at_index ascending.
    """
    pivots: list[Pivot] = []
    n = len(candles)

    for i in range(left, n - right):
        c = candles[i]
        confirmed_idx = i + right

        # Check pivot HIGH
        left_highs_ok = all(candles[j].high < c.high for j in range(i - left, i))
        right_highs_ok = all(candles[j].high < c.high for j in range(i + 1, i + right + 1))
        if left_highs_ok and right_highs_ok:
            pivots.append(Pivot(
                index=i,
                confirmed_at_index=confirmed_idx,
                timestamp=c.timestamp,
                confirmed_at=candles[confirmed_idx].timestamp,
                pivot_type=PivotType.HIGH,
                price=c.high,
                candle_high=c.high,
                candle_low=c.low,
            ))

        # Check pivot LOW
        left_lows_ok = all(candles[j].low > c.low for j in range(i - left, i))
        right_lows_ok = all(candles[j].low > c.low for j in range(i + 1, i + right + 1))
        if left_lows_ok and right_lows_ok:
            pivots.append(Pivot(
                index=i,
                confirmed_at_index=confirmed_idx,
                timestamp=c.timestamp,
                confirmed_at=candles[confirmed_idx].timestamp,
                pivot_type=PivotType.LOW,
                price=c.low,
                candle_high=c.high,
                candle_low=c.low,
            ))

    pivots.sort(key=lambda p: p.confirmed_at_index)
    return pivots


def get_pivots_known_at(pivots: list[Pivot], at_index: int) -> list[Pivot]:
    """Return only pivots whose confirmed_at_index <= at_index. No look-ahead."""
    return [p for p in pivots if p.confirmed_at_index <= at_index]
