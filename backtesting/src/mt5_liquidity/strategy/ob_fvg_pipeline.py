"""OB+FVG strategy pipeline — produces Setup objects for the backtest engine.

Scans M5 candles in a rolling-window fashion, calling detect_order_blocks(),
detect_fvgs(), and get_ob_fvg_signal() on each bar.  Signals are converted to
Setup objects whose shape matches what the backtest engine expects:

  setup.direction            — "BUY" or "SELL"
  setup.stop_loss_price      — computed inside get_ob_fvg_signal
  setup.setup_id             — unique string key
  setup.state                — SetupState.ENTRY_READY
  setup.retest               — stub with rejection_candle_index (signal bar)

A signal on bar *i* means:
  - rejection_candle_index = i  (the signal candle)
  - fill happens on bar i+1     (engine adds +1 when building fill_bar_map)

Deduplication: at most one open signal per OB origin timestamp + direction.
Once an OB fires a signal it is removed from the active set so the same zone
cannot trigger again on the next bar.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from ..data.candles import Candle
from .order_blocks import detect_order_blocks
from .fvg import detect_fvgs
from .ob_fvg import get_ob_fvg_signal
from .setup import Setup, SetupState, SetupEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Minimal stub that satisfies the engine's retest attribute access
# ---------------------------------------------------------------------------

@dataclass
class _ObFvgRetestStub:
    """Thin stub that provides the fields the backtest engine reads from retest."""
    rejection_candle_index: int
    rejection_candle_ts: datetime
    signal_price: float
    direction: str = "BUY"


# ---------------------------------------------------------------------------
# Minimal stub for the sweep attribute (Setup dataclass requires it)
# ---------------------------------------------------------------------------

@dataclass
class _ObFvgSweepStub:
    zone_id: str = "ob_fvg"
    sweep_candle_index: int = 0
    sweep_candle_ts: datetime = field(default_factory=lambda: datetime(2000, 1, 1))
    wick_extreme: float = 0.0


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run_ob_fvg_strategy(
    m5_candles: list[Candle],
    config,
    point: float,
) -> list[Setup]:
    """Scan all M5 candles and return one Setup per OB+FVG signal.

    Parameters
    ----------
    m5_candles:
        Full chronological list of M5 candles.
    config:
        BacktestConfig instance; uses ob_min_impulse_points, ob_lookback,
        fvg_min_gap_points, ob_fvg_sl_buffer_points, rr_target.
    point:
        Symbol point size.

    Returns
    -------
    list[Setup]
        All ENTRY_READY setups discovered across the full candle series.
    """
    n = len(m5_candles)
    if n < 3:
        return []

    min_impulse: float = getattr(config, "ob_min_impulse_points", 50.0)
    lookback: int = int(getattr(config, "ob_lookback", 20))
    min_gap: float = getattr(config, "fvg_min_gap_points", 10.0)
    sl_buffer: float = getattr(config, "ob_fvg_sl_buffer_points", 15.0)
    rr: float = getattr(config, "rr_target", 2.0)

    setups: list[Setup] = []
    # Track which OB origin timestamps have already triggered a signal so we
    # do not re-fire the same OB on consecutive bars.
    fired_obs: set[str] = set()
    signal_counter = 0

    # Use a bounded lookback window for both OB and FVG detection to keep
    # the per-bar cost O(lookback) rather than O(i), making the full scan
    # O(n * lookback) instead of O(n^2).
    _fvg_lookback = max(lookback * 2, 50)   # slightly wider for FVG fill checks

    for i in range(2, n):
        # Bounded window: last _fvg_lookback bars ending at bar i (inclusive).
        win_start = max(0, i + 1 - _fvg_lookback)
        window = m5_candles[win_start: i + 1]

        obs = detect_order_blocks(
            window,
            min_impulse_points=min_impulse,
            lookback=lookback,
            point=point,
        )
        fvgs = detect_fvgs(window, min_gap_points=min_gap, point=point)

        signal = get_ob_fvg_signal(
            candles=window,
            obs=obs,
            fvgs=fvgs,
            rr_target=rr,
            point=point,
            sl_buffer_points=sl_buffer,
            min_impulse_points=min_impulse,
        )
        if signal is None:
            continue

        direction, entry_price, sl_price, tp_price, confidence = signal

        # Find the OB that produced this signal for deduplication
        valid_obs_dir = [ob for ob in obs if ob.direction == direction and ob.is_valid]
        if not valid_obs_dir:
            continue
        ob = valid_obs_dir[-1]
        ob_key = f"{ob.origin_ts.isoformat()}_{direction}"

        if ob_key in fired_obs:
            continue
        fired_obs.add(ob_key)

        candle = m5_candles[i]
        signal_counter += 1
        setup_id = f"OBFVG_{signal_counter:04d}_{direction}_{i}"

        # Build a minimal Setup the engine can consume
        retest_stub = _ObFvgRetestStub(
            rejection_candle_index=i,
            rejection_candle_ts=candle.timestamp,
            signal_price=entry_price,
            direction=direction,
        )
        sweep_stub = _ObFvgSweepStub(
            zone_id=ob_key,
            sweep_candle_index=i,
            sweep_candle_ts=candle.timestamp,
            wick_extreme=ob.low if direction == "BUY" else ob.high,
        )

        setup = Setup(
            setup_id=setup_id,
            direction=direction,
            sweep=sweep_stub,  # type: ignore[arg-type]
            retest=retest_stub,  # type: ignore[arg-type]
            state=SetupState.ENTRY_READY,
            stop_loss_price=sl_price,
            signal_price=entry_price,
        )
        setup.events.append(SetupEvent(
            state=SetupState.ENTRY_READY,
            timestamp=candle.timestamp,
            candle_index=i,
            detail=(
                f"OB+FVG {direction} signal: entry={entry_price} "
                f"sl={sl_price} tp={tp_price} conf={confidence:.4f}"
            ),
            price=entry_price,
        ))

        logger.debug(
            "OB+FVG signal %s at bar %d (%s): dir=%s entry=%.2f sl=%.2f tp=%.2f conf=%.4f",
            setup_id, i, candle.timestamp.isoformat(), direction,
            entry_price, sl_price, tp_price, confidence,
        )

        setups.append(setup)

    return setups
