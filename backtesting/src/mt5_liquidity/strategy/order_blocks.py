"""Order Block detector for XAUUSD M5 candles.

An Order Block (OB) is the last opposing candle before a strong impulsive move.

Definitions
-----------
Bullish OB:
    The last *bearish* candle immediately before a bullish impulse that is at
    least `min_impulse_points` points.  Impulse size = sum of body sizes of the
    two candles that follow the OB candle (candles[i+1] and candles[i+2]).

Bearish OB:
    The last *bullish* candle immediately before a bearish impulse of the same
    minimum size.

Invalidation:
    An OB is marked ``is_valid = False`` when a later candle *body* (open/close
    range) fully closes beyond the OB's low (bullish) or high (bearish).

Usage
-----
    from mt5_liquidity.strategy.order_blocks import detect_order_blocks

    obs = detect_order_blocks(candles, min_impulse_points=50.0, lookback=20)
    valid_obs = [ob for ob in obs if ob.is_valid]

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

def _o(c) -> float:
    return c["open"] if isinstance(c, dict) else c.open

def _h(c) -> float:
    return c["high"] if isinstance(c, dict) else c.high

def _l(c) -> float:
    return c["low"] if isinstance(c, dict) else c.low

def _cl(c) -> float:
    return c["close"] if isinstance(c, dict) else c.close

def _ts(c) -> datetime:
    return c["timestamp"] if isinstance(c, dict) else c.timestamp


def _body_size(c) -> float:
    """Absolute body size (|close - open|)."""
    return abs(_cl(c) - _o(c))


def _is_bearish(c) -> bool:
    return _cl(c) < _o(c)


def _is_bullish(c) -> bool:
    return _cl(c) > _o(c)


# ─────────────────────────────────────────────────────────────────────────────
# Public dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OrderBlock:
    """One detected order block zone."""
    direction: str              # "BUY" (bullish OB) or "SELL" (bearish OB)
    high: float                 # OB candle high
    low: float                  # OB candle low
    origin_ts: datetime         # timestamp of the OB candle
    impulse_size_points: float  # sum of the two following candles' body sizes
    is_valid: bool              # False once price body fully closes beyond OB


# ─────────────────────────────────────────────────────────────────────────────
# Detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_order_blocks(
    candles: list,
    min_impulse_points: float = 50.0,
    lookback: int = 20,
    point: float = 0.01,
) -> list[OrderBlock]:
    """Detect order blocks in a candle series.

    Parameters
    ----------
    candles:
        Chronological list of candles (oldest first).  Accepts both
        ``shared.models.Candle`` objects and plain dicts.
    min_impulse_points:
        Minimum impulse size in *points* (not price).  The impulse is the sum
        of body sizes of the two candles that follow the candidate OB candle.
        Default 50 (= 0.50 USD for XAUUSD where point = 0.01).
    lookback:
        How many candles from the *end* of the series to scan for new OB
        candidates.  Candles beyond this window are still used for
        invalidation checking but are not re-evaluated as new OBs.  Set to
        ``len(candles)`` to scan the entire series.
    point:
        Symbol point size.  Used to convert the impulse threshold from points
        to price units.

    Returns
    -------
    list[OrderBlock]
        All detected OBs (valid and invalid), newest last.
    """
    n = len(candles)
    if n < 3:
        return []

    min_impulse_price = min_impulse_points * point
    obs: list[OrderBlock] = []

    # We need candle[i], candle[i+1], candle[i+2] to form an OB, so the
    # scan window stops at n-3 (inclusive).
    scan_start = max(0, n - lookback - 2)  # a bit of overlap for safety
    scan_end   = n - 2                      # i+2 must be within bounds

    for i in range(scan_start, scan_end):
        c0 = candles[i]
        c1 = candles[i + 1]
        c2 = candles[i + 2]
        impulse = _body_size(c1) + _body_size(c2)

        if impulse < min_impulse_price:
            continue

        # Bullish OB: c0 is bearish, the following impulse is bullish
        if _is_bearish(c0) and _cl(c1) > _o(c1) and _cl(c2) > _o(c2):
            ob = OrderBlock(
                direction="BUY",
                high=_h(c0),
                low=_l(c0),
                origin_ts=_ts(c0),
                impulse_size_points=round(impulse / point, 1),
                is_valid=True,
            )
            obs.append(ob)

        # Bearish OB: c0 is bullish, the following impulse is bearish
        elif _is_bullish(c0) and _cl(c1) < _o(c1) and _cl(c2) < _o(c2):
            ob = OrderBlock(
                direction="SELL",
                high=_h(c0),
                low=_l(c0),
                origin_ts=_ts(c0),
                impulse_size_points=round(impulse / point, 1),
                is_valid=True,
            )
            obs.append(ob)

    # -------------------------------------------------------------------------
    # Invalidation pass — scan ALL candles after each OB's index to see if
    # price body closed fully beyond the OB.
    # -------------------------------------------------------------------------
    for ob in obs:
        # Find the index of the OB candle so we only check subsequent candles.
        ob_idx = None
        for idx, c in enumerate(candles):
            if _ts(c) == ob.origin_ts:
                ob_idx = idx
                break
        if ob_idx is None:
            continue

        for c in candles[ob_idx + 1:]:
            body_top    = max(_o(c), _cl(c))
            body_bottom = min(_o(c), _cl(c))

            if ob.direction == "BUY":
                # Invalidated when body fully closes below OB low
                if body_top < ob.low and _cl(c) < ob.low:
                    ob.is_valid = False
                    break
            else:  # SELL
                # Invalidated when body fully closes above OB high
                if body_bottom > ob.high and _cl(c) > ob.high:
                    ob.is_valid = False
                    break

    return obs
