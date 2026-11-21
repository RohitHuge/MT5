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
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from shared.backtest_http import build_app, start_in_thread
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

    def __init__(self, overrides: dict | None = None) -> None:
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

        # Latest indicator state, refreshed each candle for the snapshot loop
        self._last_state: dict = {}

        self._apply_overrides(overrides or {})

    def _apply_overrides(self, overrides: dict) -> None:
        """Apply backtest param overrides post-construction.

        Params are read from the environment in __init__, so a backtest has to
        set the attributes explicitly — mutating os.environ is not thread-safe
        while the live loop is running.
        """
        typed = {
            "rsi_min_buy": float,
            "rsi_max_sell": float,
            "risk_pct": float,
            "rr_target": float,
        }
        for key, cast in typed.items():
            if key in overrides and overrides[key] is not None:
                try:
                    setattr(self, key, cast(overrides[key]))
                except (TypeError, ValueError):
                    log.warning("Ignoring invalid override %s=%r", key, overrides[key])

    def _get_indicator_state(self) -> dict:
        return dict(self._last_state)

    def _indicator_snapshot(self, candles: list[Candle]) -> dict:
        """EMA levels and RSI at the latest candle — for chart overlays only.

        Mirrors the seeding in _detect_last_signal rather than modifying it, so
        the proven signal path stays untouched.
        """
        if len(candles) < _WARMUP + 1:
            return {}

        k9, k21, k50 = 2.0 / 10, 2.0 / 22, 2.0 / 51
        ema9 = sum(c.close for c in candles[:9]) / 9.0
        ema21 = sum(c.close for c in candles[:21]) / 21.0
        ema50 = sum(c.close for c in candles[:50]) / 50.0

        for c in candles[9:50]:
            ema9 = _ema_step(ema9, c.close, k9)
        for c in candles[21:50]:
            ema21 = _ema_step(ema21, c.close, k21)

        deltas = [candles[j].close - candles[j - 1].close for j in range(50, 50 + _RSI_PERIOD)]
        avg_gain = sum(max(d, 0.0) for d in deltas) / _RSI_PERIOD
        avg_loss = sum(abs(min(d, 0.0)) for d in deltas) / _RSI_PERIOD

        for c in candles[50: 50 + _RSI_PERIOD]:
            ema9 = _ema_step(ema9, c.close, k9)
            ema21 = _ema_step(ema21, c.close, k21)
            ema50 = _ema_step(ema50, c.close, k50)

        prev_close = candles[_WARMUP - 1].close
        for candle in candles[_WARMUP:]:
            ema9 = _ema_step(ema9, candle.close, k9)
            ema21 = _ema_step(ema21, candle.close, k21)
            ema50 = _ema_step(ema50, candle.close, k50)
            delta = candle.close - prev_close
            avg_gain = (avg_gain * (_RSI_PERIOD - 1) + max(delta, 0.0)) / _RSI_PERIOD
            avg_loss = (avg_loss * (_RSI_PERIOD - 1) + abs(min(delta, 0.0))) / _RSI_PERIOD
            prev_close = candle.close

        rsi = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss) if avg_loss > 0.0 else 100.0
        return {
            "ema9": round(ema9, 5),
            "ema21": round(ema21, 5),
            "ema50": round(ema50, 5),
            "rsi": round(rsi, 2),
        }


    def on_candle(self, candles: list[Candle]) -> Signal | None:
        self._last_state = self._indicator_snapshot(candles)

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


# ─────────────────────────────────────────────────────────────────────────────
# Backtest — same signal logic as the live path, one bar at a time
# ─────────────────────────────────────────────────────────────────────────────

def _run_backtest(candles: list[Candle], params: dict) -> list[dict]:
    """Replay the strategy over historical candles and resolve fills.

    A fresh instance is used so the live dedup state is untouched. fetch_equity
    is replaced with a fixed simulated equity: it is one blocking HTTP call per
    bar otherwise, and a 0.0 return would collapse every position to vol_min.
    """
    from shared.backtest_sim import simulate_trades

    svc = EmaScalpStrategy(overrides=params)
    simulated_equity = float(params.get("equity", 10000.0))
    svc.fetch_equity = lambda: simulated_equity  # type: ignore[method-assign]

    signals: list[dict] = []
    last_ts = None

    for i in range(_WARMUP + 1, len(candles) + 1):
        # Growing prefix, capped at the live deque size — this is exactly what
        # the strategy's rolling window looks like at each live candle. A fixed
        # 65-candle slice does NOT work: the detector only reports a crossover
        # it finds while scanning, so a minimal window returns almost nothing.
        chunk = candles[max(0, i - svc.window_size): i]
        result = _detect_last_signal(
            chunk,
            point=svc.point,
            rsi_min_buy=svc.rsi_min_buy,
            rsi_max_sell=svc.rsi_max_sell,
        )
        if result is None:
            continue

        direction, signal_ts, sl_price = result
        # The detector reports the most recent crossover in the window; only
        # act on each one once.
        if signal_ts == last_ts:
            continue
        last_ts = signal_ts

        entry_price = chunk[-1].close
        sl_distance = abs(entry_price - sl_price)
        if sl_distance < svc.point:
            continue

        tp_price = (
            entry_price + sl_distance * svc.rr_target if direction == "BUY"
            else entry_price - sl_distance * svc.rr_target
        )
        signals.append({
            "direction": direction,
            "entry_price": entry_price,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "lots": svc.calc_lots(simulated_equity, svc.risk_pct, sl_distance),
            "signal_ts": signal_ts,
            "index": i - 1,
        })

    return simulate_trades(
        candles, signals, point=svc.point, tick_value=svc.tick_value
    )


http_app = build_app("ema-scalp", _run_backtest)


if __name__ == "__main__":
    start_in_thread(http_app)
    EmaScalpStrategy().run()
