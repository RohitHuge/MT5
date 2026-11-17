"""EMA 9/21/50 crossover + RSI14 scalping signals for M5 charts.

Strategy logic:
  BUY  when EMA9 crosses above EMA21 AND close > EMA50 AND RSI > rsi_min_buy
  SELL when EMA9 crosses below EMA21 AND close < EMA50 AND RSI < rsi_max_sell

RSI filter removes weak crossovers where momentum hasn't confirmed the direction.
SL is placed beyond the local swing low/high (last sl_swing_bars candles) plus
a configurable buffer.  TP is computed by the engine as RR × SL distance.

Warm-up: 64 candles (50 for EMA50, 14 extra for RSI14 seed average).

No MT5 imports. No I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..data.candles import Candle

_RSI_PERIOD = 14
_WARMUP = 50 + _RSI_PERIOD   # 64 candles consumed before first signal


@dataclass
class ScalpSignal:
    """One entry signal emitted by the EMA crossover + RSI strategy."""
    signal_id: str
    direction: str              # "BUY" or "SELL"
    signal_candle_index: int    # fill executes on the NEXT candle
    signal_candle_ts: datetime
    sl_price: float             # stop-loss price at signal time
    rsi: float                  # RSI value at signal time (for diagnostics)


def _ema_step(prev: float, price: float, k: float) -> float:
    return price * k + prev * (1.0 - k)


def detect_scalp_signals(
    candles: list[Candle],
    point: float,
    sl_swing_bars: int = 4,
    sl_buffer_points: int = 15,
    rsi_min_buy: float = 55.0,
    rsi_max_sell: float = 45.0,
) -> list[ScalpSignal]:
    """Return all filtered EMA crossover signals in the candle series.

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
    rsi_min_buy:
        Minimum RSI14 required to allow a BUY signal (default 55).
    rsi_max_sell:
        Maximum RSI14 required to allow a SELL signal (default 45).
    """
    if len(candles) < _WARMUP + 1:
        return []

    k9  = 2.0 / (9  + 1)
    k21 = 2.0 / (21 + 1)
    k50 = 2.0 / (50 + 1)

    # --- Seed EMAs from their respective SMA windows ---
    ema9  = sum(c.close for c in candles[:9])  / 9.0
    ema21 = sum(c.close for c in candles[:21]) / 21.0
    ema50 = sum(c.close for c in candles[:50]) / 50.0

    # Advance EMA9/21 to synchronise all three at the end of candle index 49
    for c in candles[9:50]:
        ema9  = _ema_step(ema9,  c.close, k9)
    for c in candles[21:50]:
        ema21 = _ema_step(ema21, c.close, k21)

    # --- Seed RSI14 using Wilder's smoothing ---
    # Compute first 14 price changes (candles 50..63)
    deltas = [candles[j].close - candles[j - 1].close for j in range(50, 50 + _RSI_PERIOD)]
    gains  = [max(d, 0.0) for d in deltas]
    losses = [abs(min(d, 0.0)) for d in deltas]
    avg_gain = sum(gains)  / _RSI_PERIOD
    avg_loss = sum(losses) / _RSI_PERIOD

    # Advance EMAs through the RSI warm-up window (candles 50..63) as well
    for c in candles[50 : 50 + _RSI_PERIOD]:
        ema9  = _ema_step(ema9,  c.close, k9)
        ema21 = _ema_step(ema21, c.close, k21)
        ema50 = _ema_step(ema50, c.close, k50)

    prev_above = ema9 > ema21
    prev_close = candles[_WARMUP - 1].close

    signals: list[ScalpSignal] = []
    buf = sl_buffer_points * point

    for i in range(_WARMUP, len(candles)):
        candle = candles[i]

        # --- Update EMAs ---
        ema9  = _ema_step(ema9,  candle.close, k9)
        ema21 = _ema_step(ema21, candle.close, k21)
        ema50 = _ema_step(ema50, candle.close, k50)

        # --- Update RSI14 (Wilder's method) ---
        delta    = candle.close - prev_close
        gain     = max(delta, 0.0)
        loss     = abs(min(delta, 0.0))
        avg_gain = (avg_gain * (_RSI_PERIOD - 1) + gain)  / _RSI_PERIOD
        avg_loss = (avg_loss * (_RSI_PERIOD - 1) + loss)  / _RSI_PERIOD
        rsi      = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss) if avg_loss > 0.0 else 100.0
        prev_close = candle.close

        curr_above = ema9 > ema21

        if curr_above != prev_above:                  # crossover on this candle
            look_back = max(0, i - sl_swing_bars + 1)

            if curr_above and candle.close > ema50 and rsi > rsi_min_buy:
                swing_low = min(c.low for c in candles[look_back : i + 1])
                sl = round(swing_low - buf, 10)
                signals.append(ScalpSignal(
                    signal_id=f"SCALP_{i:05d}_BUY",
                    direction="BUY",
                    signal_candle_index=i,
                    signal_candle_ts=candle.timestamp,
                    sl_price=sl,
                    rsi=round(rsi, 2),
                ))

            elif (not curr_above) and candle.close < ema50 and rsi < rsi_max_sell:
                swing_high = max(c.high for c in candles[look_back : i + 1])
                sl = round(swing_high + buf, 10)
                signals.append(ScalpSignal(
                    signal_id=f"SCALP_{i:05d}_SELL",
                    direction="SELL",
                    signal_candle_index=i,
                    signal_candle_ts=candle.timestamp,
                    sl_price=sl,
                    rsi=round(rsi, 2),
                ))

        prev_above = curr_above

    return signals
