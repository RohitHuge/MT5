"""EMA 9/21/50 + RSI14 scalping strategy container.

Wraps the backtester's detect_scalp_signals logic (inlined here so this
container has no dependency on the backtesting package or MetaTrader5).

Signal logic (identical to backtesting/src/mt5_liquidity/strategy/scalp_ema.py):
  BUY  when EMA9 crosses above EMA21, close > EMA50, RSI > rsi_min_buy
  SELL when EMA9 crosses below EMA21, close < EMA50, RSI < rsi_max_sell
  SL   = swing low/high of last sl_swing_bars candles ± sl_buffer_points

Environment variables:
  MODE, REDIS_URL, MT5_BRIDGE_URL — handled by StrategyBase
  STRATEGY_NAME  default "ema-scalp"
  EMA_FAST       default 9
  EMA_SLOW       default 21
  EMA_TREND      default 50
  RSI_PERIOD     default 14
  RSI_MIN_BUY    default 55.0
  RSI_MAX_SELL   default 45.0
  RISK_PCT       default 0.005  (0.5 %)
  RR_TARGET      default 1.5
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# shared/ is two levels up from this file; add repo root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.models import Candle, Signal
from shared.strategy_base import StrategyBase

log = logging.getLogger(__name__)

_RSI_PERIOD = 14
_WARMUP = 50 + _RSI_PERIOD   # 64 candles before first signal is possible


# ─────────────────────────────────────────────────────────────────────────────
# Pure signal detection — no I/O, no MT5 imports
# (inlined from backtesting/src/mt5_liquidity/strategy/scalp_ema.py)
# ─────────────────────────────────────────────────────────────────────────────

def _ema_step(prev: float, price: float, k: float) -> float:
    return price * k + prev * (1.0 - k)


def _detect_last_signal(
    candles: list[Candle],
    point: float,
    sl_swing_bars: int = 4,
    sl_buffer_points: int = 15,
    rsi_min_buy: float = 55.0,
    rsi_max_sell: float = 45.0,
) -> tuple[str, float, float] | None:
    """Return (direction, signal_candle_ts_iso, sl_price) for the most recent
    crossover signal, or None if fewer than _WARMUP+1 candles or no signal."""
    if len(candles) < _WARMUP + 1:
        return None

    k9  = 2.0 / (9  + 1)
    k21 = 2.0 / (21 + 1)
    k50 = 2.0 / (50 + 1)

    ema9  = sum(c.close for c in candles[:9])  / 9.0
    ema21 = sum(c.close for c in candles[:21]) / 21.0
    ema50 = sum(c.close for c in candles[:50]) / 50.0

    for c in candles[9:50]:
        ema9  = _ema_step(ema9,  c.close, k9)
    for c in candles[21:50]:
        ema21 = _ema_step(ema21, c.close, k21)

    deltas = [candles[j].close - candles[j - 1].close for j in range(50, 50 + _RSI_PERIOD)]
    gains  = [max(d, 0.0) for d in deltas]
    losses = [abs(min(d, 0.0)) for d in deltas]
    avg_gain = sum(gains)  / _RSI_PERIOD
    avg_loss = sum(losses) / _RSI_PERIOD

    for c in candles[50: 50 + _RSI_PERIOD]:
        ema9  = _ema_step(ema9,  c.close, k9)
        ema21 = _ema_step(ema21, c.close, k21)
        ema50 = _ema_step(ema50, c.close, k50)

    prev_above = ema9 > ema21
    prev_close = candles[_WARMUP - 1].close
    buf = sl_buffer_points * point

    last: tuple[str, datetime, float] | None = None

    for i in range(_WARMUP, len(candles)):
        candle = candles[i]
        ema9  = _ema_step(ema9,  candle.close, k9)
        ema21 = _ema_step(ema21, candle.close, k21)
        ema50 = _ema_step(ema50, candle.close, k50)

        delta    = candle.close - prev_close
        gain     = max(delta, 0.0)
        loss     = abs(min(delta, 0.0))
        avg_gain = (avg_gain * (_RSI_PERIOD - 1) + gain)  / _RSI_PERIOD
        avg_loss = (avg_loss * (_RSI_PERIOD - 1) + loss)  / _RSI_PERIOD
        rsi      = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss) if avg_loss > 0.0 else 100.0
        prev_close = candle.close

        curr_above = ema9 > ema21

        if curr_above != prev_above:
            look_back = max(0, i - sl_swing_bars + 1)

            if curr_above and candle.close > ema50 and rsi > rsi_min_buy:
                swing_low = min(c.low for c in candles[look_back: i + 1])
                sl = round(swing_low - buf, 10)
                last = ("BUY", candle.timestamp, sl)

            elif (not curr_above) and candle.close < ema50 and rsi < rsi_max_sell:
                swing_high = max(c.high for c in candles[look_back: i + 1])
                sl = round(swing_high + buf, 10)
                last = ("SELL", candle.timestamp, sl)

        prev_above = curr_above

    return last   # (direction, candle_ts, sl_price) or None


# ─────────────────────────────────────────────────────────────────────────────
# Strategy container
# ─────────────────────────────────────────────────────────────────────────────

class EmaScalpStrategy(StrategyBase):

    def __init__(self) -> None:
        name = os.getenv("STRATEGY_NAME", "ema-scalp")
        super().__init__(
            name=name,
            symbol=os.getenv("SYMBOL", "XAUUSD"),
            timeframe=os.getenv("TIMEFRAME", "M5"),
            window_size=200,   # 200 M5 candles ≈ ~16 hours; well above 64-candle warmup
        )
        self.rsi_min_buy  = float(os.getenv("RSI_MIN_BUY",  "55"))
        self.rsi_max_sell = float(os.getenv("RSI_MAX_SELL", "45"))
        self.risk_pct     = float(os.getenv("RISK_PCT",     "0.005"))
        self.rr_target    = float(os.getenv("RR_TARGET",    "1.5"))

        # Dedup: skip if we already emitted a signal for this candle timestamp
        self._last_signal_ts: datetime | None = None

    def on_candle(self, candles: list[Candle]) -> Signal | None:
        result = _detect_last_signal(
            candles,
            point=self.point,
            rsi_min_buy=self.rsi_min_buy,
            rsi_max_sell=self.rsi_max_sell,
        )
        if result is None:
            return None

        direction, signal_ts, sl_price = result

        # Only emit if this is a new crossover (different candle timestamp)
        if self._last_signal_ts is not None and signal_ts <= self._last_signal_ts:
            return None

        # Entry price = last candle close (market order fills near here)
        entry_price = candles[-1].close

        sl_distance = abs(entry_price - sl_price)
        if sl_distance < self.point:
            log.debug("SL distance too small (%.5f), skipping", sl_distance)
            return None

        tp_price = (
            entry_price + sl_distance * self.rr_target if direction == "BUY"
            else entry_price - sl_distance * self.rr_target
        )

        equity = self.fetch_equity()
        lots = self.calc_lots(equity, self.risk_pct, sl_distance)

        self._last_signal_ts = signal_ts

        return Signal(
            strategy=self.name,
            symbol=self.symbol,
            direction=direction,
            entry_price=round(entry_price, 2),
            sl_price=round(sl_price, 2),
            tp_price=round(tp_price, 2),
            lots=lots,
            confidence=0.6,
            candle_ts=signal_ts,
            emitted_at=datetime.now(timezone.utc),
            mode=self.mode,
            comment=f"ema-scalp rsi={self.rsi_min_buy}/{self.rsi_max_sell}",
        )


if __name__ == "__main__":
    EmaScalpStrategy().run()
