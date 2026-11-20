"""Liquidity Sweep strategy container.

Wraps the backtester's run_liquidity_sweep_strategy pipeline for live use.
Receives M5 candles from Redis, internally aggregates to M15, re-runs the
full sweep → MSS → retest pipeline on each new candle, and emits a Signal
for every new ENTRY_READY setup found.

The backtesting source is available at /app/backtesting_src (added to
PYTHONPATH in the Dockerfile). None of those files import MetaTrader5 at
the strategy layer.

Environment variables:
  MODE, REDIS_URL, MT5_BRIDGE_URL — handled by StrategyBase
  STRATEGY_NAME     default "liq-sweep"
  PIVOT_LEFT        default 5
  PIVOT_RIGHT       default 3
  ZONE_TOLERANCE    default 10  (points)
  RISK_PCT          default 0.005 (0.5%)
  RR_TARGET         default 2.0
"""
from __future__ import annotations

import logging
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# shared/ two levels up; backtesting_src added by Dockerfile via PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from shared.models import Candle, Signal
from shared.strategy_base import StrategyBase

# Backtesting strategy imports — no MetaTrader5 in this import chain
from mt5_liquidity.strategy.liquidity_sweep import run_liquidity_sweep_strategy
from mt5_liquidity.strategy.setup import SetupState
from mt5_liquidity.data.candles import Candle as BtCandle

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Minimal config shim — matches the fields BacktestConfig exposes to the pipeline
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _PipelineConfig:
    pivot_left: int           = 5
    pivot_right: int          = 3
    zone_tolerance_points: int = 10
    sweep_min_points: int     = 5
    mss_timeout_bars: int     = 20
    retest_timeout_bars: int  = 10
    retest_band_points: int   = 5
    sl_buffer_points: int     = 5


# ─────────────────────────────────────────────────────────────────────────────
# M5 → M15 aggregation (inlined; no dependency on backtesting data module)
# ─────────────────────────────────────────────────────────────────────────────

def _aggregate_m5_to_m15(m5_candles: list[BtCandle]) -> list[BtCandle]:
    """Group complete M5 triplets into M15 candles (UTC 15-min boundaries)."""
    groups: dict = defaultdict(list)
    for c in m5_candles:
        ts = c.timestamp
        boundary_min = (ts.minute // 15) * 15
        key = ts.replace(minute=boundary_min, second=0, microsecond=0)
        groups[key].append(c)

    m15: list[BtCandle] = []
    for key in sorted(groups):
        group = groups[key]
        if len(group) == 3:
            m15.append(BtCandle(
                timestamp=key,
                open=group[0].open,
                high=max(c.high for c in group),
                low=min(c.low for c in group),
                close=group[-1].close,
                volume=sum(c.volume for c in group),
                spread=group[-1].spread,
                timeframe="M15",
            ))
    return m15


def _to_bt_candle(c: Candle) -> BtCandle:
    """Convert shared Candle to backtesting Candle (same fields, different types)."""
    return BtCandle(
        timestamp=c.timestamp,
        open=c.open,
        high=c.high,
        low=c.low,
        close=c.close,
        volume=float(c.volume),
        spread=float(c.spread),
        timeframe=c.timeframe,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Strategy container
# ─────────────────────────────────────────────────────────────────────────────

class LiquiditySweepStrategy(StrategyBase):

    def __init__(self) -> None:
        name = os.getenv("STRATEGY_NAME", "liq-sweep")
        super().__init__(
            name=name,
            symbol=os.getenv("SYMBOL", "XAUUSD"),
            timeframe=os.getenv("TIMEFRAME", "M5"),
            # 500 M5 candles ≈ ~41 hours — enough M15 history for meaningful zones
            window_size=500,
        )
        self.risk_pct  = float(os.getenv("RISK_PCT",  "0.005"))
        self.rr_target = float(os.getenv("RR_TARGET", "2.0"))

        self._config = _PipelineConfig(
            pivot_left=int(os.getenv("PIVOT_LEFT",     "5")),
            pivot_right=int(os.getenv("PIVOT_RIGHT",   "3")),
            zone_tolerance_points=int(os.getenv("ZONE_TOLERANCE", "10")),
        )

        # Dedup: set of (sweep_candle_ts_iso, direction) already signalled
        self._emitted: set[tuple[str, str]] = set()

    def on_candle(self, candles: list[Candle]) -> Signal | None:
        if len(candles) < 100:   # need enough history for meaningful pivots/zones
            return None

        bt_m5 = [_to_bt_candle(c) for c in candles]
        bt_m15 = _aggregate_m5_to_m15(bt_m5)

        if len(bt_m15) < 20:    # need enough M15 candles for zone detection
            return None

        try:
            setups = run_liquidity_sweep_strategy(
                m15_candles=bt_m15,
                m5_candles=bt_m5,
                config=self._config,
                point=self.point,
            )
        except Exception as exc:
            log.debug("Pipeline error: %s", exc)
            return None

        # Find the most recent new ENTRY_READY setup
        entry_setups = [s for s in setups if s.state == SetupState.ENTRY_READY]
        if not entry_setups:
            return None

        # Sort by rejection candle timestamp descending
        entry_setups.sort(key=lambda s: s.retest.rejection_candle_ts, reverse=True)

        for setup in entry_setups:
            dedup_key = (setup.sweep.sweep_candle_ts.isoformat(), setup.direction)
            if dedup_key in self._emitted:
                continue

            self._emitted.add(dedup_key)

            entry_price = setup.signal_price
            sl_price = setup.stop_loss_price

            if entry_price is None or sl_price is None:
                continue

            sl_distance = abs(entry_price - sl_price)
            if sl_distance < self.point:
                log.debug("SL distance too small (%.5f), skipping", sl_distance)
                continue

            tp_price = (
                entry_price + sl_distance * self.rr_target
                if setup.direction == "BUY"
                else entry_price - sl_distance * self.rr_target
            )

            equity = self.fetch_equity()
            lots = self.calc_lots(equity, self.risk_pct, sl_distance)

            return Signal(
                strategy=self.name,
                symbol=self.symbol,
                direction=setup.direction,
                entry_price=round(entry_price, 2),
                sl_price=round(sl_price, 2),
                tp_price=round(tp_price, 2),
                lots=lots,
                confidence=0.65,
                candle_ts=setup.retest.rejection_candle_ts,
                emitted_at=datetime.now(timezone.utc),
                mode=self.mode,
                comment=f"liq-sweep setup={setup.setup_id}",
            )

        return None   # all recent setups already emitted


if __name__ == "__main__":
    LiquiditySweepStrategy().run()
