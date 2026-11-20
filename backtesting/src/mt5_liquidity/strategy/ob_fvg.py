"""OB + FVG confluence signal generator.

Combines Order Block and Fair Value Gap detectors to find high-probability
entry zones where both a valid OB and an unfilled FVG overlap.

Logic
-----
1. Take the most recent *valid* OB in each direction (BUY / SELL).
2. Find all *unfilled* FVGs in the same direction that overlap with the OB
   zone.  Overlap condition::

       fvg.gap_high > ob.low  AND  fvg.gap_low < ob.high

3. Check whether the current candle close has *entered* the overlap zone::

       overlap_low  = max(ob.low, fvg.gap_low)
       overlap_high = min(ob.high, fvg.gap_high)
       overlap_low <= candle.close <= overlap_high

4. If the entry condition is met, compute SL / TP and return a signal tuple.

Signal tuple layout
-------------------
(direction, entry_price, sl_price, tp_price, confidence)

  direction   : "BUY" or "SELL"
  entry_price : last candle close (market order)
  sl_price    : ob.low − buffer (BUY)  /  ob.high + buffer (SELL)
  tp_price    : entry ± (abs(entry − sl) × rr_target)
  confidence  : min(1.0, impulse_size / (min_impulse_points * 2))

Returns None if no confluence is found.

Deduplication
-------------
The caller is responsible for preventing duplicate signals from the same OB.
The recommended key is ``(ob.origin_ts.isoformat(), direction)``.

Candle compatibility
--------------------
Accepts both ``shared.models.Candle`` objects and plain dicts with keys
``open``, ``high``, ``low``, ``close``, ``timestamp``.
"""
from __future__ import annotations

from .order_blocks import OrderBlock
from .fvg import FVG


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _cl(c) -> float:
    return c["close"] if isinstance(c, dict) else c.close


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def get_ob_fvg_signal(
    candles: list,
    obs: list[OrderBlock],
    fvgs: list[FVG],
    rr_target: float = 2.0,
    point: float = 0.01,
    sl_buffer_points: float = 15.0,
    min_impulse_points: float = 50.0,
) -> tuple | None:
    """Return an OB+FVG confluence signal or None.

    Parameters
    ----------
    candles:
        Rolling window of candles in chronological order (oldest first).
        Needs at least one candle — the last candle provides the entry price.
    obs:
        Output of ``detect_order_blocks()``.
    fvgs:
        Output of ``detect_fvgs()``.
    rr_target:
        Risk-reward ratio used to compute the take-profit level.
    point:
        Symbol point size.
    sl_buffer_points:
        Extra buffer beyond the OB boundary when placing the stop-loss.
    min_impulse_points:
        Used for confidence calculation only (denominator = min_impulse * 2).

    Returns
    -------
    tuple | None
        ``(direction, entry_price, sl_price, tp_price, confidence)``
        or ``None`` if no confluence found on the current candle.
    """
    if not candles:
        return None

    current = candles[-1]
    current_close = _cl(current)
    sl_buffer = sl_buffer_points * point

    # Try both directions, prefer BUY over SELL if both trigger simultaneously
    for direction in ("BUY", "SELL"):
        # Most recent valid OB in this direction (obs list is oldest-first)
        valid_obs_dir = [
            ob for ob in obs
            if ob.direction == direction and ob.is_valid
        ]
        if not valid_obs_dir:
            continue
        ob = valid_obs_dir[-1]   # most recent

        # Active (unfilled) FVGs in the same direction that overlap with OB
        overlapping_fvgs = [
            fvg for fvg in fvgs
            if (
                fvg.direction == direction
                and not fvg.is_filled
                and fvg.gap_high > ob.low
                and fvg.gap_low  < ob.high
            )
        ]
        if not overlapping_fvgs:
            continue

        # For the signal we use the FVG with the largest overlap (most confluence)
        def _overlap_size(fvg: FVG) -> float:
            return min(fvg.gap_high, ob.high) - max(fvg.gap_low, ob.low)

        best_fvg = max(overlapping_fvgs, key=_overlap_size)

        overlap_low  = max(ob.low,  best_fvg.gap_low)
        overlap_high = min(ob.high, best_fvg.gap_high)

        if overlap_low >= overlap_high:
            continue

        # Entry condition: current candle close is inside the overlap zone
        if not (overlap_low <= current_close <= overlap_high):
            continue

        # Compute levels
        entry_price = current_close

        if direction == "BUY":
            sl_price = ob.low - sl_buffer
            tp_price = entry_price + abs(entry_price - sl_price) * rr_target
        else:
            sl_price = ob.high + sl_buffer
            tp_price = entry_price - abs(entry_price - sl_price) * rr_target

        # Confidence: how strong was the impulse that created the OB
        denominator = min_impulse_points * 2.0
        confidence  = min(1.0, ob.impulse_size_points / denominator) if denominator > 0 else 0.5

        return (
            direction,
            round(entry_price, 2),
            round(sl_price, 2),
            round(tp_price, 2),
            round(confidence, 4),
        )

    return None
