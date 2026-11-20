"""OB + FVG confluence strategy container.

Receives M5 candles from Redis, detects Order Blocks and Fair Value Gaps, and
emits a Signal when price enters an OB+FVG overlap zone.

Environment variables
---------------------
  MODE, REDIS_URL, MT5_BRIDGE_URL — handled by StrategyBase
  STRATEGY_NAME       default "ob-fvg"
  MIN_IMPULSE_POINTS  default 50.0  — minimum OB impulse filter
  MIN_GAP_POINTS      default 10.0  — minimum FVG size filter
  OB_LOOKBACK         default 20    — how many recent candles to scan for OBs
  RISK_PCT            default 0.005 (0.5 %)
  RR_TARGET           default 2.0
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# shared/ is two levels up from this file; repo root → sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

# backtesting_src is added to PYTHONPATH by the Dockerfile; also add it here
# for local runs where PYTHONPATH may not be set.
_bt_src = Path(__file__).resolve().parents[0] / "backtesting_src"
if _bt_src.exists() and str(_bt_src) not in sys.path:
    sys.path.insert(0, str(_bt_src))

from shared.models import Candle, Signal
from shared.strategy_base import StrategyBase

from mt5_liquidity.strategy.order_blocks import detect_order_blocks
from mt5_liquidity.strategy.fvg import detect_fvgs
from mt5_liquidity.strategy.ob_fvg import get_ob_fvg_signal

log = logging.getLogger(__name__)


class ObFvgStrategy(StrategyBase):
    """Order Block + Fair Value Gap confluence strategy."""

    def __init__(self) -> None:
        name = os.getenv("STRATEGY_NAME", "ob-fvg")
        super().__init__(
            name=name,
            symbol=os.getenv("SYMBOL", "XAUUSD"),
            timeframe=os.getenv("TIMEFRAME", "M5"),
            # Keep 300 M5 candles (~25 hours) — enough history for OB/FVG context
            window_size=300,
        )

        self.min_impulse_points = float(os.getenv("MIN_IMPULSE_POINTS", "50.0"))
        self.min_gap_points     = float(os.getenv("MIN_GAP_POINTS",     "10.0"))
        self.ob_lookback        = int(os.getenv("OB_LOOKBACK",          "20"))
        self.risk_pct           = float(os.getenv("RISK_PCT",           "0.005"))
        self.rr_target          = float(os.getenv("RR_TARGET",          "2.0"))

        # Dedup set: keyed by (ob_origin_ts_iso, direction)
        # Prevents re-emitting a signal for the same OB on every new candle
        # while price remains inside the OB+FVG zone.
        self._emitted: set[tuple[str, str]] = set()

    def on_candle(self, candles: list[Candle]) -> Signal | None:
        # Need at least lookback + 2 extra candles for the OB pattern check
        if len(candles) < max(self.ob_lookback + 2, 20):
            return None

        # ------------------------------------------------------------------ #
        # 1. Detect structures                                                 #
        # ------------------------------------------------------------------ #
        obs = detect_order_blocks(
            candles,
            min_impulse_points=self.min_impulse_points,
            lookback=self.ob_lookback,
            point=self.point,
        )
        fvgs = detect_fvgs(
            candles,
            min_gap_points=self.min_gap_points,
            point=self.point,
        )

        valid_obs = [ob for ob in obs if ob.is_valid]
        active_fvgs = [fvg for fvg in fvgs if not fvg.is_filled]

        if not valid_obs or not active_fvgs:
            return None

        # ------------------------------------------------------------------ #
        # 2. Check for confluence signal on the current candle                #
        # ------------------------------------------------------------------ #
        result = get_ob_fvg_signal(
            candles,
            obs=valid_obs,
            fvgs=active_fvgs,
            rr_target=self.rr_target,
            point=self.point,
            min_impulse_points=self.min_impulse_points,
        )

        if result is None:
            return None

        direction, entry_price, sl_price, tp_price, confidence = result

        # ------------------------------------------------------------------ #
        # 3. Dedup: find the OB that triggered this signal                    #
        # ------------------------------------------------------------------ #
        # The OB used is the most recent valid OB in the signal's direction —
        # same selection logic as get_ob_fvg_signal().
        triggering_ob = None
        for ob in reversed(valid_obs):
            if ob.direction == direction:
                triggering_ob = ob
                break

        if triggering_ob is None:
            return None

        dedup_key = (triggering_ob.origin_ts.isoformat(), direction)
        if dedup_key in self._emitted:
            return None

        self._emitted.add(dedup_key)

        # Prune dedup set when it grows too large (memory guard)
        if len(self._emitted) > 100:
            self._emitted.clear()
            log.debug("Dedup set cleared (exceeded 100 entries)")

        # ------------------------------------------------------------------ #
        # 4. Size the position                                                 #
        # ------------------------------------------------------------------ #
        sl_distance = abs(entry_price - sl_price)
        if sl_distance < self.point:
            log.debug("SL distance too small (%.5f), skipping", sl_distance)
            return None

        equity = self.fetch_equity()
        lots   = self.calc_lots(equity, self.risk_pct, sl_distance)

        log.info(
            "OB+FVG signal: %s entry=%.2f sl=%.2f tp=%.2f conf=%.2f ob_ts=%s",
            direction, entry_price, sl_price, tp_price, confidence,
            triggering_ob.origin_ts.isoformat(),
        )

        return Signal(
            strategy=self.name,
            symbol=self.symbol,
            direction=direction,
            entry_price=entry_price,
            sl_price=sl_price,
            tp_price=tp_price,
            lots=lots,
            confidence=confidence,
            candle_ts=candles[-1].timestamp,
            emitted_at=datetime.now(timezone.utc),
            mode=self.mode,
            comment=(
                f"ob-fvg ob={triggering_ob.origin_ts.isoformat()} "
                f"impulse={triggering_ob.impulse_size_points}pts"
            ),
        )


if __name__ == "__main__":
    ObFvgStrategy().run()
