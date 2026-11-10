"""Setup state machine: sweep → MSS → retest → ENTRY_READY. No MT5 imports."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .market_structure import MSSSignal, detect_mss
from .models import LiquidityZone, Pivot, ZoneType
from .retests import RetestSignal, detect_retest
from .sweeps import SweepSignal, detect_sweeps

logger = logging.getLogger(__name__)


class SetupState(Enum):
    SWEEP_CONFIRMED = "SWEEP_CONFIRMED"
    MSS_CONFIRMED   = "MSS_CONFIRMED"
    RETEST_PENDING  = "RETEST_PENDING"   # MSS done; waiting for retest
    ENTRY_READY     = "ENTRY_READY"      # rejection candle confirmed
    EXPIRED         = "EXPIRED"          # MSS timeout elapsed
    MISSED          = "MISSED"           # retest timeout elapsed


@dataclass
class SetupEvent:
    state: SetupState
    timestamp: datetime
    candle_index: int
    detail: str
    price: float | None = None


@dataclass
class Setup:
    setup_id: str
    direction: str               # "BUY" or "SELL"
    sweep: SweepSignal
    mss: MSSSignal | None = None
    retest: RetestSignal | None = None
    state: SetupState = SetupState.SWEEP_CONFIRMED
    events: list[SetupEvent] = field(default_factory=list)
    entry_price: float | None = None
    stop_loss_price: float | None = None
    sl_buffer_points: int = 5

    def transition(
        self,
        new_state: SetupState,
        timestamp: datetime,
        candle_index: int,
        detail: str,
        price: float | None = None,
    ) -> None:
        self.state = new_state
        self.events.append(SetupEvent(new_state, timestamp, candle_index, detail, price))

    @property
    def is_terminal(self) -> bool:
        return self.state in (
            SetupState.ENTRY_READY,
            SetupState.EXPIRED,
            SetupState.MISSED,
        )


def _find_m5_index_after_m15_close(m5_candles: list, m15_sweep_ts: datetime) -> int:
    """
    Return the index of the first M5 candle whose timestamp is STRICTLY AFTER
    the M15 sweep candle's timestamp (i.e. the first M5 bar opened after the
    sweep candle closed).  Returns len(m5_candles) if none found.
    """
    for i, c in enumerate(m5_candles):
        if c.timestamp > m15_sweep_ts:
            return i
    return len(m5_candles)


def run_pipeline(
    m15_candles: list,
    m5_candles: list,
    m5_pivots: list[Pivot],
    zones: list[LiquidityZone],
    config,
    point: float,
) -> list[Setup]:
    """
    Full Phase 3 pipeline — one pass, no future data.

    1. Detect M15 sweeps on the supplied zones (zones already carry their
       creation indices so no-look-ahead is enforced inside detect_sweeps).
    2. For each sweep find the first M5 bar after the sweep candle's close.
    3. Run MSS detection from that bar.
    4. If MSS found, run retest detection.
    5. Build Setup with full state-transition log.
    6. Each zone produces at most one Setup.

    SL:
      SELL: sl = sweep.wick_extreme + sl_buffer_points * point
      BUY:  sl = sweep.wick_extreme - sl_buffer_points * point

    Returns all Setups (ENTRY_READY, EXPIRED, MISSED) for auditability.
    """
    sl_buf = getattr(config, "sl_buffer_points", 5)
    retest_band_pts = getattr(config, "retest_band_points", 5)

    sweeps = detect_sweeps(
        m15_candles=m15_candles,
        zones=zones,
        min_sweep_points=config.sweep_min_points,
        point=point,
    )

    setups: list[Setup] = []
    seen_zones: set[str] = set()

    for sweep_idx, sweep in enumerate(sweeps):
        if sweep.zone_id in seen_zones:
            continue  # one setup per zone
        seen_zones.add(sweep.zone_id)

        direction = "SELL" if sweep.zone_type == ZoneType.BSL else "BUY"
        setup_id = f"SETUP_{sweep_idx:04d}_{direction}_{sweep.sweep_candle_index}"

        # SL placement
        sl_dist = sl_buf * point
        if direction == "SELL":
            sl = round(sweep.wick_extreme + sl_dist, 10)
        else:
            sl = round(sweep.wick_extreme - sl_dist, 10)

        setup = Setup(
            setup_id=setup_id,
            direction=direction,
            sweep=sweep,
            sl_buffer_points=sl_buf,
            stop_loss_price=sl,
        )
        setup.transition(
            SetupState.SWEEP_CONFIRMED,
            sweep.sweep_candle_ts,
            sweep.sweep_candle_index,
            f"{sweep.zone_type.value} zone {sweep.zone_id} swept; wick={sweep.wick_extreme}",
            price=sweep.wick_extreme,
        )

        # Find first M5 bar after sweep candle close
        m5_start = _find_m5_index_after_m15_close(m5_candles, sweep.sweep_candle_ts)

        if m5_start >= len(m5_candles):
            setup.transition(
                SetupState.EXPIRED,
                sweep.sweep_candle_ts,
                sweep.sweep_candle_index,
                "No M5 candles available after sweep",
            )
            setups.append(setup)
            continue

        mss = detect_mss(
            m5_candles=m5_candles,
            m5_pivots=m5_pivots,
            sweep=sweep,
            sweep_m5_index=m5_start,
            mss_timeout_bars=config.mss_timeout_bars,
            point=point,
        )

        if mss is None:
            timeout_candle = m5_candles[min(m5_start + config.mss_timeout_bars - 1, len(m5_candles) - 1)]
            setup.transition(
                SetupState.EXPIRED,
                timeout_candle.timestamp,
                min(m5_start + config.mss_timeout_bars - 1, len(m5_candles) - 1),
                f"MSS timeout after {config.mss_timeout_bars} M5 bars",
            )
            setups.append(setup)
            continue

        setup.mss = mss
        setup.transition(
            SetupState.MSS_CONFIRMED,
            mss.break_candle_ts,
            mss.break_candle_index,
            (
                f"{mss.direction} MSS: closed {mss.break_candle_close} "
                f"through pivot {mss.broken_pivot_price} "
                f"(confirmed at bar {mss.broken_pivot_confirmed_at})"
            ),
            price=mss.break_candle_close,
        )

        retest_start_ts = mss.break_candle_ts
        setup.transition(
            SetupState.RETEST_PENDING,
            retest_start_ts,
            mss.break_candle_index,
            "Waiting for retest of structure level",
            price=mss.broken_pivot_price,
        )

        retest = detect_retest(
            m5_candles=m5_candles,
            mss=mss,
            mss_candle_index=mss.break_candle_index,
            retest_band_points=retest_band_pts,
            retest_timeout_bars=config.retest_timeout_bars,
            point=point,
        )

        if retest is None:
            timeout_idx = min(
                mss.break_candle_index + config.retest_timeout_bars,
                len(m5_candles) - 1,
            )
            timeout_candle = m5_candles[timeout_idx]
            setup.transition(
                SetupState.MISSED,
                timeout_candle.timestamp,
                timeout_idx,
                f"Retest timeout after {config.retest_timeout_bars} M5 bars; "
                "price never returned to structure level",
            )
            setups.append(setup)
            continue

        setup.retest = retest
        setup.entry_price = retest.entry_price
        setup.transition(
            SetupState.ENTRY_READY,
            retest.rejection_candle_ts,
            retest.rejection_candle_index,
            (
                f"{retest.direction} rejection at {retest.rejection_candle_close}; "
                f"entry={retest.entry_price}, sl={sl}"
            ),
            price=retest.entry_price,
        )

        setups.append(setup)

    return setups
