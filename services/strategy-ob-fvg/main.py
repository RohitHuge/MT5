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

from shared.backtest_http import build_app, start_in_thread
from shared.models import Candle, Signal
from shared.strategy_base import StrategyBase

from mt5_liquidity.strategy.order_blocks import detect_order_blocks
from mt5_liquidity.strategy.fvg import detect_fvgs
from mt5_liquidity.strategy.ob_fvg import get_ob_fvg_signal

log = logging.getLogger(__name__)


class ObFvgStrategy(StrategyBase):
    """Order Block + Fair Value Gap confluence strategy."""

    def __init__(self, overrides: dict | None = None) -> None:
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

        # Latest structure state, refreshed each candle for the snapshot loop
        self._last_state: dict = {}

        self._apply_overrides(overrides or {})

    def _apply_overrides(self, overrides: dict) -> None:
        """Apply backtest param overrides post-construction.

        Params come from the environment in __init__, so overrides are applied
        as attributes rather than by mutating os.environ (not thread-safe while
        the live loop is running).
        """
        typed = {
            "min_impulse_points": float,
            "min_gap_points": float,
            "ob_lookback": int,
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

    def _record_state(self, valid_obs: list, active_fvgs: list) -> None:
        """Cache the OB/FVG zones for the snapshot loop's chart overlay."""
        self._last_state = {
            "ob_zones": [
                {
                    "timeStart": ob.origin_ts.isoformat(),
                    "high": round(ob.high, 2),
                    "low": round(ob.low, 2),
                    "direction": ob.direction,
                }
                for ob in valid_obs[-20:]
            ],
            "fvg_zones": [
                {
                    "timeStart": fvg.origin_ts.isoformat(),
                    "high": round(fvg.gap_high, 2),
                    "low": round(fvg.gap_low, 2),
                    "direction": fvg.direction,
                }
                for fvg in active_fvgs[-20:]
            ],
        }


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

        self._record_state(valid_obs, active_fvgs)

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


# ─────────────────────────────────────────────────────────────────────────────
# Backtest — drives the live on_candle() over historical candles
# ─────────────────────────────────────────────────────────────────────────────

def _run_backtest(candles: list[Candle], params: dict) -> list[dict]:
    """Replay the strategy and resolve fills.

    on_candle() is already per-candle (it reads candles[-1] and dedups by OB
    origin), so the simulation feeds it a growing window exactly like the live
    loop does — no duplicated detection logic. A fresh instance keeps the live
    dedup set untouched, and fetch_equity is stubbed (it is a blocking HTTP call
    per bar, and 0.0 would collapse every position to vol_min).
    """
    from shared.backtest_sim import simulate_trades

    svc = ObFvgStrategy(overrides=params)
    simulated_equity = float(params.get("equity", 10000.0))
    svc.fetch_equity = lambda: simulated_equity  # type: ignore[method-assign]

    signals: list[dict] = []
    window = svc.window_size

    for i in range(1, len(candles) + 1):
        chunk = candles[max(0, i - window): i]
        try:
            signal = svc.on_candle(chunk)
        except Exception as exc:
            log.debug("backtest on_candle failed at %d: %s", i, exc)
            continue
        if signal is None:
            continue
        signals.append({
            "direction": signal.direction,
            "entry_price": signal.entry_price,
            "sl_price": signal.sl_price,
            "tp_price": signal.tp_price,
            "lots": signal.lots,
            "signal_ts": signal.candle_ts,
            "index": i - 1,
        })

    return simulate_trades(
        candles, signals, point=svc.point, tick_value=svc.tick_value
    )


http_app = build_app("ob-fvg", _run_backtest)


if __name__ == "__main__":
    start_in_thread(http_app)
    ObFvgStrategy().run()
