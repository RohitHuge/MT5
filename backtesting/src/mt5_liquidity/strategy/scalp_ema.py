"""EMA 9/21/50 crossover scalping signals for M5 charts.

Strategy logic:
  BUY  when EMA9 crosses above EMA21 AND close > EMA50 (trend confirms)
  SELL when EMA9 crosses below EMA21 AND close < EMA50 (trend confirms)

SL is placed beyond the local swing low/high (last sl_swing_bars candles)
plus a configurable buffer.  TP is computed by the engine as RR × SL distance.

Warm-up: first 50 candles are consumed seeding the EMAs; no signals fire
during that window.

No MT5 imports. No I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..data.candles import Candle


@dataclass
class ScalpSignal:
    """One entry signal emitted by the EMA crossover strategy."""
    signal_id: str
    direction: str              # "BUY" or "SELL"
    signal_candle_index: int    # fill executes on the NEXT candle
    signal_candle_ts: datetime
    sl_price: float             # stop-loss price at signal time


def _ema_step(prev: float, price: float, k: float) -> float:
    return price * k + prev * (1.0 - k)


def detect_scalp_signals(
    candles: list[Candle],
    point: float,
    sl_swing_bars: int = 4,
    sl_buffer_points: int = 15,
) -> list[ScalpSignal]:
    """Return all EMA crossover signals in the candle series.

    Parameters
    ----------
    candles:
        Full M5 series in chronological order (UTC-aware timestamps).
    point:
        Symbol point size (e.g. 0.01 for XAUUSD).
    sl_swing_bars:
        How many bars to look back (including the signal bar) for the
        local swing high/low that anchors the stop-loss.
    sl_buffer_points:
        Extra points beyond the swing extreme before placing the SL.
    """
    if len(candles) < 51:
        return []

    k9  = 2.0 / (9  + 1)
    k21 = 2.0 / (21 + 1)
    k50 = 2.0 / (50 + 1)

    # Seed from SMA of the warm-up window for each period
    ema9  = sum(c.close for c in candles[:9])  / 9.0
    ema21 = sum(c.close for c in candles[:21]) / 21.0
    ema50 = sum(c.close for c in candles[:50]) / 50.0

    # Advance EMA9 and EMA21 through the remaining warm-up candles so all
    # three are synchronised at the boundary of candle index 49.
    for c in candles[9:50]:
        ema9  = _ema_step(ema9,  c.close, k9)
    for c in candles[21:50]:
        ema21 = _ema_step(ema21, c.close, k21)

    prev_above = ema9 > ema21   # relationship just before the first live candle

    signals: list[ScalpSignal] = []
    buf = sl_buffer_points * point

    for i in range(50, len(candles)):
        candle = candles[i]

        ema9  = _ema_step(ema9,  candle.close, k9)
        ema21 = _ema_step(ema21, candle.close, k21)
        ema50 = _ema_step(ema50, candle.close, k50)

        curr_above = ema9 > ema21

        if curr_above != prev_above:                 # a crossover just happened
            look_back = max(0, i - sl_swing_bars + 1)

            if curr_above and candle.close > ema50:  # BUY — trend up
                swing_low = min(c.low for c in candles[look_back : i + 1])
                sl = round(swing_low - buf, 10)
                signals.append(ScalpSignal(
                    signal_id=f"SCALP_{i:05d}_BUY",
                    direction="BUY",
                    signal_candle_index=i,
                    signal_candle_ts=candle.timestamp,
                    sl_price=sl,
                ))

            elif (not curr_above) and candle.close < ema50:  # SELL — trend down
                swing_high = max(c.high for c in candles[look_back : i + 1])
                sl = round(swing_high + buf, 10)
                signals.append(ScalpSignal(
                    signal_id=f"SCALP_{i:05d}_SELL",
                    direction="SELL",
                    signal_candle_index=i,
                    signal_candle_ts=candle.timestamp,
                    sl_price=sl,
                ))

        prev_above = curr_above

    return signals
