"""Backtest engine: wire strategy signals into fills, positions, and ledger.

No MT5 imports. No order_send. No live trading.

Flow:
  1. Run the Phase 3 liquidity-sweep strategy to get a list of Setups.
  2. Filter to ENTRY_READY setups only.
  3. Walk the M5 candle series chronologically:
     a. For each bar, check all open positions for SL/TP.
     b. If the bar index == setup's signal_candle + 1 (fill bar), try to open a position:
        - Skip if max_positions already open.
        - Skip if daily loss limit reached (max_daily_loss_pct of initial equity).
        - Size the position using calculate_position_size.
        - Simulate fill via simulate_fill.
        - Open an OpenPosition.
  4. At end of candle series, force-close any still-open positions at last candle close.
  5. Return BacktestResult.

No look-ahead: fills happen on the bar AFTER the signal candle.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from ..config import BacktestConfig
from ..data.candles import Candle
from ..strategy.liquidity_sweep import run_liquidity_sweep_strategy
from ..strategy.setup import Setup, SetupState
from .execution import FillResult, simulate_fill
from .ledger import LedgerStats, TradeLedger
from .positions import ClosedPosition, OpenPosition, PositionStatus
from ..risk.position_sizing import SizingResult, calculate_position_size

logger = logging.getLogger(__name__)


@dataclass
class SymbolMeta:
    """Minimal symbol metadata needed by the engine (from MT5Adapter or hard-coded for tests)."""
    point: float
    tick_value: float
    tick_size: float
    volume_min: float
    volume_max: float
    volume_step: float


@dataclass
class BacktestResult:
    setups_total: int
    setups_entry_ready: int
    setups_skipped_max_pos: int
    setups_skipped_daily_loss: int
    setups_skipped_sizing: int
    setups_skipped_no_next_bar: int
    trades_opened: int
    closed_positions: list[ClosedPosition]
    force_closed: int
    stats: LedgerStats
    equity_curve: list[tuple[datetime, float]]   # (candle_ts, equity_after_close)
    rejected_sizings: list[SizingResult]


def run_backtest(
    m15_candles: list[Candle],
    m5_candles: list[Candle],
    config: BacktestConfig,
    symbol_meta: SymbolMeta,
) -> BacktestResult:
    """
    Full Phase 4 backtest run.

    1. Run Phase 3 strategy → list[Setup]
    2. Collect ENTRY_READY setups sorted by signal candle index.
    3. Walk M5 bars chronologically:
       - Close positions on SL/TP.
       - Open new positions on fill bars (next bar after signal).
    4. Force-close at end of data.
    5. Compute statistics.
    """
    point     = symbol_meta.point
    tick_val  = symbol_meta.tick_value
    tick_size = symbol_meta.tick_size

    # value_per_point_per_lot used for P&L ($/point/lot)
    value_per_point_per_lot = (tick_val / tick_size) * point

    # --- Phase 3 ---
    all_setups: list[Setup] = run_liquidity_sweep_strategy(
        m15_candles=m15_candles,
        m5_candles=m5_candles,
        config=config,
        point=point,
    )

    entry_setups = [s for s in all_setups if s.state == SetupState.ENTRY_READY]
    entry_setups.sort(key=lambda s: s.retest.rejection_candle_index)

    # Map fill_bar_index → list[Setup] so engine can process without inner scan
    fill_bar_map: dict[int, list[Setup]] = {}
    for s in entry_setups:
        fill_idx = s.retest.rejection_candle_index + 1
        fill_bar_map.setdefault(fill_idx, []).append(s)

    # --- Engine state ---
    initial_equity = config.initial_equity
    equity = initial_equity
    ledger = TradeLedger(initial_equity=initial_equity)
    open_positions: list[OpenPosition] = []
    equity_curve: list[tuple[datetime, float]] = []

    # Daily loss tracking
    day_start_equity: dict[date, float] = {}

    counters = {
        "skipped_max_pos": 0,
        "skipped_daily_loss": 0,
        "skipped_sizing": 0,
        "skipped_no_next_bar": 0,
        "trades_opened": 0,
        "force_closed": 0,
    }
    rejected_sizings: list[SizingResult] = []

    for idx, candle in enumerate(m5_candles):
        candle_date = candle.timestamp.date()

        # Initialise daily equity baseline for this day
        if candle_date not in day_start_equity:
            day_start_equity[candle_date] = equity

        # 1. Resolve open positions against this candle (closes first)
        still_open: list[OpenPosition] = []
        for pos in open_positions:
            status, close_price = pos.check_candle(candle, idx)
            if status == PositionStatus.OPEN:
                still_open.append(pos)
            else:
                exit_reason = "TP" if status == PositionStatus.HIT_TP else "SL"
                closed = pos.close(close_price, idx, candle.timestamp, exit_reason)
                ledger.record(closed)
                equity += closed.pnl_usd
                equity_curve.append((candle.timestamp, equity))
                logger.debug("Closed %s via %s pnl=%.2f equity=%.2f", closed.setup_id, exit_reason, closed.pnl_usd, equity)
        open_positions = still_open

        # 2. Attempt fills for setups whose fill bar is this candle
        for setup in fill_bar_map.get(idx, []):
            # --- guard: max simultaneous positions ---
            if len(open_positions) >= config.max_positions:
                counters["skipped_max_pos"] += 1
                logger.debug("Skipped %s — max_positions reached", setup.setup_id)
                continue

            # --- guard: daily loss limit ---
            day_baseline = day_start_equity.get(candle_date, initial_equity)
            daily_loss_usd = equity - day_baseline  # negative = loss
            daily_loss_pct = abs(daily_loss_usd) / day_baseline * 100.0 if day_baseline > 0 else 0.0
            if daily_loss_usd < 0 and daily_loss_pct >= config.max_daily_loss_pct:
                counters["skipped_daily_loss"] += 1
                logger.debug(
                    "Skipped %s — daily loss %.2f%% >= limit %.2f%%",
                    setup.setup_id, daily_loss_pct, config.max_daily_loss_pct,
                )
                continue

            # --- size position ---
            sizing = calculate_position_size(
                signal_price=setup.signal_price,
                stop_loss_price=setup.stop_loss_price,
                equity=equity,
                risk_pct=config.risk_pct,
                volume_min=symbol_meta.volume_min,
                volume_max=symbol_meta.volume_max,
                volume_step=symbol_meta.volume_step,
                tick_value=tick_val,
                tick_size=tick_size,
                point=point,
            )
            if sizing.rejection_reason is not None:
                counters["skipped_sizing"] += 1
                rejected_sizings.append(sizing)
                logger.debug("Skipped %s — sizing rejected: %s", setup.setup_id, sizing.rejection_reason)
                continue

            # --- simulate fill ---
            fill = simulate_fill(
                setup_id=setup.setup_id,
                direction=setup.direction,
                signal_candle_index=setup.retest.rejection_candle_index,
                sl_price=setup.stop_loss_price,
                lots=sizing.lots,
                m5_candles=m5_candles,
                spread_points=config.spread_points,
                slippage_points=config.slippage_points,
                rr_target=config.rr_target,
                point=point,
            )
            if fill is None:
                counters["skipped_no_next_bar"] += 1
                logger.debug("Skipped %s — no next bar for fill", setup.setup_id)
                continue

            open_positions.append(OpenPosition(
                fill=fill,
                value_per_point_per_lot=value_per_point_per_lot,
                point=point,
            ))
            counters["trades_opened"] += 1
            logger.info(
                "Opened %s %s fill=%.2f sl=%.2f tp=%.2f lots=%.2f",
                fill.setup_id, fill.direction, fill.fill_price, fill.sl_price, fill.tp_price, fill.lots,
            )

    # 3. Force-close remaining open positions at last candle close
    if m5_candles and open_positions:
        last_candle = m5_candles[-1]
        for pos in open_positions:
            closed = pos.close(last_candle.close, len(m5_candles) - 1, last_candle.timestamp, "FORCE_CLOSE")
            ledger.record(closed)
            equity += closed.pnl_usd
            equity_curve.append((last_candle.timestamp, equity))
            counters["force_closed"] += 1
            logger.info("Force-closed %s pnl=%.2f", closed.setup_id, closed.pnl_usd)

    stats = ledger.compute_stats()

    return BacktestResult(
        setups_total=len(all_setups),
        setups_entry_ready=len(entry_setups),
        setups_skipped_max_pos=counters["skipped_max_pos"],
        setups_skipped_daily_loss=counters["skipped_daily_loss"],
        setups_skipped_sizing=counters["skipped_sizing"],
        setups_skipped_no_next_bar=counters["skipped_no_next_bar"],
        trades_opened=counters["trades_opened"],
        closed_positions=ledger.trades,
        force_closed=counters["force_closed"],
        stats=stats,
        equity_curve=equity_curve,
        rejected_sizings=rejected_sizings,
    )
