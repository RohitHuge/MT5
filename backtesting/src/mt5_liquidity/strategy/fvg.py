"""Fair Value Gap (FVG) detector for XAUUSD M5 candles.

An FVG is a three-candle pattern where the price moves so quickly that a gap
is left between candle[i-1] and candle[i+1] with candle[i] inside the gap.
The gap is a *liquidity imbalance* that price often returns to fill.

Definitions
-----------
Bullish FVG (imbalance to the upside):
    ``candle[i-1].high < candle[i+1].low``
    Gap zone: low = candle[i-1].high, high = candle[i+1].low

Bearish FVG (imbalance to the downside):
    ``candle[i-1].low > candle[i+1].high``
    Gap zone: low = candle[i+1].high, high = candle[i-1].low

Fill detection:
    A later candle ``closes inside`` the gap zone:
    ``candle.close >= gap_low AND candle.close <= gap_high``

Usage
-----
    from mt5_liquidity.strategy.fvg import detect_fvgs

    fvgs = detect_fvgs(candles, min_gap_points=10.0)
    active = [f for f in fvgs if not f.is_filled]

Candle compatibility
--------------------
Accepts both ``shared.models.Candle`` objects and plain dicts with keys
``open``, ``high``, ``low``, ``close``, ``timestamp``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _h(c) -> float:
    return c["high"] if isinstance(c, dict) else c.high

def _l(c) -> float:
    return c["low"] if isinstance(c, dict) else c.low

def _cl(c) -> float:
    return c["close"] if isinstance(c, dict) else c.close

def _ts(c) -> datetime:
    return c["timestamp"] if isinstance(c, dict) else c.timestamp


# ─────────────────────────────────────────────────────────────────────────────
# Public dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FVG:
    """One detected Fair Value Gap zone."""
    direction: str          # "BUY" (bullish FVG) or "SELL" (bearish FVG)
    gap_high: float         # top of the imbalance zone
    gap_low: float          # bottom of the imbalance zone
    origin_ts: datetime     # timestamp of the middle candle (candle[i])
    gap_size_points: float  # (gap_high - gap_low) / point
    is_filled: bool         # True when a later candle close falls inside zone


# ─────────────────────────────────────────────────────────────────────────────
# Detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_fvgs(
    candles: list,
    min_gap_points: float = 10.0,
    point: float = 0.01,
) -> list[FVG]:
    """Detect Fair Value Gaps in a candle series.

    Parameters
    ----------
    candles:
        Chronological list of candles (oldest first).  Accepts both
        ``shared.models.Candle`` objects and plain dicts.
    min_gap_points:
        Minimum gap size in *points*.  Gaps smaller than this are ignored.
        Default 10 (= 0.10 USD for XAUUSD where point = 0.01).
    point:
        Symbol point size used to calculate ``gap_size_points`` and apply the
        minimum gap filter.

    Returns
    -------
    list[FVG]
        All detected FVGs (filled and unfilled), newest last.
    """
    n = len(candles)
    if n < 3:
        return []

    min_gap_price = min_gap_points * point
    fvgs: list[FVG] = []

    # Pattern requires candle[i-1], candle[i], candle[i+1] so i ranges [1, n-2]
    for i in range(1, n - 1):
        prev = candles[i - 1]
        curr = candles[i]
        nxt  = candles[i + 1]

        # Bullish FVG: gap above — prev.high < nxt.low
        if _h(prev) < _l(nxt):
            gap_low  = _h(prev)
            gap_high = _l(nxt)
            if (gap_high - gap_low) >= min_gap_price:
                fvgs.append(FVG(
                    direction="BUY",
                    gap_high=gap_high,
                    gap_low=gap_low,
                    origin_ts=_ts(curr),
                    gap_size_points=round((gap_high - gap_low) / point, 1),
                    is_filled=False,
                ))

        # Bearish FVG: gap below — prev.low > nxt.high
        elif _l(prev) > _h(nxt):
            gap_low  = _h(nxt)
            gap_high = _l(prev)
            if (gap_high - gap_low) >= min_gap_price:
                fvgs.append(FVG(
                    direction="SELL",
                    gap_high=gap_high,
                    gap_low=gap_low,
                    origin_ts=_ts(curr),
                    gap_size_points=round((gap_high - gap_low) / point, 1),
                    is_filled=False,
                ))

    # -------------------------------------------------------------------------
    # Fill pass — mark FVGs whose zone has been touched by a later candle close.
    # -------------------------------------------------------------------------
    for fvg in fvgs:
        # Find the index of the middle candle (origin_ts).
        origin_idx = None
        for idx, c in enumerate(candles):
            if _ts(c) == fvg.origin_ts:
                origin_idx = idx
                break
        if origin_idx is None:
            continue

        # Check candles after the pattern (i+2 onwards)
        for c in candles[origin_idx + 2:]:
            close = _cl(c)
            if fvg.gap_low <= close <= fvg.gap_high:
                fvg.is_filled = True
                break

    return fvgs
