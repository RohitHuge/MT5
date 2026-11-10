"""Backtest engine: wire strategy signals into fills, positions, and ledger.

No MT5 imports. No order_send. No live trading.

Flow:
  1. Run the Phase 3 liquidity-sweep strategy to get a list of Setups.
  2. Filter to ENTRY_READY setups only.
  3. Walk the M5 candle series chronologically:
     a. For each bar, check all open positions for SL/TP.
     b. If the bar index == setup's signal_candle + 1 (fill bar):
        - Skip if max_positions already open.
        - Skip if daily loss limit reached (realised + unrealised equity vs daily baseline).
        - Compute the actual fill price via compute_fill_price (Fix 1).
        - Size the position using the ACTUAL fill price (not signal_price) so monetary
          risk matches the configured risk_pct (Fix 1).
        - Simulate fill and open an OpenPosition with spread_points stored (Fix 3).
        - Immediately evaluate the fill candle for SL/TP (Fix 2).
     c. Daily loss guard includes unrealised floating P&L of open positions (Fix 5).
  4. At end of candle series, force-close any still-open positions at last candle close.
  5. Compute statistics via ledger.

Commission model (Fix 4):
  commission_usd in BacktestConfig is a per-lot per-side charge.
  Round-trip commission = 2 * config.commission_usd * lots.
  Commission is deducted at close time (the equity update on close).

Daily loss baseline (Fix 5):
  current_equity = realised_equity + sum(unrealised_pnl of open positions)
  The baseline resets at each new UTC trading day boundary.
  Existing open positions are NOT force-closed when the limit is breached —
  only new entries are blocked.

Symbol metadata validation (Fix 6):
  validate_symbol_meta() must return None before the engine runs.
  The CLI no longer provides a hardcoded fallback.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..config import BacktestConfig
from ..data.candles import Candle
from ..strategy.liquidity_sweep import run_liquidity_sweep_strategy
from ..strategy.setup import Setup, SetupState
from .execution import FillResult, compute_fill_price, simulate_fill
from .ledger import LedgerStats, TradeLedger
from .positions import ClosedPosition, OpenPosition, PositionStatus
from ..risk.position_sizing import SizingResult, calculate_position_size

logger = logging.getLogger(__name__)


@dataclass
class SymbolMeta:
    """Minimal symbol metadata needed by the engine (from MT5Adapter or supplied for tests)."""
    point: float
    tick_value: float
    tick_size: float
    volume_min: float
    volume_max: float
    volume_step: float


def validate_symbol_meta(meta: SymbolMeta, symbol: str = "") -> str | None:
    """
    Validate that SymbolMeta has usable values.

    Returns an error string if invalid, None if the metadata is acceptable.
    The engine refuses to run if this returns a non-None value.
    """
    label = f"{symbol} " if symbol else ""
    if meta.point <= 0:
        return f"{label}point={meta.point} must be > 0"
    if meta.tick_size <= 0:
        return f"{label}tick_size={meta.tick_size} must be > 0"
    if meta.tick_value <= 0:
        return f"{label}tick_value={meta.tick_value} must be > 0"
    if meta.volume_min <= 0:
        return f"{label}volume_min={meta.volume_min} must be > 0"
    if meta.volume_step <= 0:
        return f"{label}volume_step={meta.volume_step} must be > 0"
    if meta.volume_max <= 0:
        return f"{label}volume_max={meta.volume_max} must be > 0"
    if meta.volume_min > meta.volume_max:
        return f"{label}volume_min {meta.volume_min} > volume_max {meta.volume_max}"
    if meta.volume_step > meta.volume_max:
        return f"{label}volume_step {meta.volume_step} > volume_max {meta.volume_max}"
    return None


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

    Raises ValueError if symbol_meta fails validation.
    """
    meta_error = validate_symbol_meta(symbol_meta)
    if meta_error:
        raise ValueError(f"Invalid symbol metadata: {meta_error}")

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

    # Map fill_bar_index → list[Setup]
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

    # Daily loss tracking uses the configured trading-day timezone. Baseline is
    # realised equity at the first observed candle of each trading day; positions
    # carried overnight are marked to market (including estimated round-trip
    # commission) against that day's baseline.
    try:
        trading_tz = ZoneInfo(config.trading_day_timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown trading_day_timezone: {config.trading_day_timezone}") from exc
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
        if candle.timestamp.tzinfo is None or candle.timestamp.utcoffset() is None:
            raise ValueError("Backtest candles must have timezone-aware timestamps")
        candle_date = candle.timestamp.astimezone(trading_tz).date()

        # Initialise daily equity baseline for this day
        if candle_date not in day_start_equity:
            day_start_equity[candle_date] = equity

        # 1. Resolve open positions against this candle
        still_open: list[OpenPosition] = []
        for pos in open_positions:
            status, close_price = pos.check_candle(candle, idx)
            if status == PositionStatus.OPEN:
                still_open.append(pos)
            else:
                exit_reason = "TP" if status == PositionStatus.HIT_TP else "SL"
                commission = 2.0 * config.commission_usd * pos.fill.lots  # round-trip
                closed = pos.close(close_price, idx, candle.timestamp, exit_reason, commission)
                ledger.record(closed)
                equity += closed.net_pnl_usd
                equity_curve.append((candle.timestamp, equity))
                logger.debug(
                    "Closed %s via %s gross=%.2f comm=%.2f net=%.2f equity=%.2f",
                    closed.setup_id, exit_reason, closed.pnl_usd,
                    closed.commission_usd, closed.net_pnl_usd, equity,
                )
        open_positions = still_open

        # 2. Attempt fills for setups whose fill bar is this candle
        for setup in fill_bar_map.get(idx, []):
            # --- guard: max simultaneous positions ---
            if len(open_positions) >= config.max_positions:
                counters["skipped_max_pos"] += 1
                logger.debug("Skipped %s — max_positions reached", setup.setup_id)
                continue

            # Fix 5: daily loss guard uses realised equity + unrealised floating P&L
            day_baseline = day_start_equity.get(candle_date, initial_equity)
            unrealised_gross = sum(pos.unrealized_pnl(candle) for pos in open_positions)
            estimated_open_commission = sum(
                2.0 * config.commission_usd * pos.fill.lots for pos in open_positions
            )
            unrealised_net = unrealised_gross - estimated_open_commission
            current_equity_with_floating = equity + unrealised_net
            daily_loss_usd = current_equity_with_floating - day_baseline
            if daily_loss_usd < 0:
                daily_loss_pct = abs(daily_loss_usd) / day_baseline * 100.0 if day_baseline > 0 else 0.0
                if daily_loss_pct >= config.max_daily_loss_pct:
                    counters["skipped_daily_loss"] += 1
                    logger.debug(
                        "Skipped %s — daily loss %.2f%% >= limit %.2f%% (unrealised_net=%.2f)",
                        setup.setup_id, daily_loss_pct, config.max_daily_loss_pct, unrealised_net,
                    )
                    continue

            # Fix 1: compute actual fill price BEFORE sizing so monetary risk is correct
            fill_idx = idx  # the current candle IS the fill candle
            if fill_idx >= len(m5_candles):
                counters["skipped_no_next_bar"] += 1
                continue

            fill_candle = m5_candles[fill_idx]
            actual_fill_price = compute_fill_price(
                setup.direction,
                fill_candle.open,
                config.spread_points,
                config.slippage_points,
                point,
            )

            # --- size position using actual fill price (not signal_price) ---
            sizing = calculate_position_size(
                signal_price=actual_fill_price,      # Fix 1: use fill price
                stop_loss_price=setup.stop_loss_price,
                equity=current_equity_with_floating,
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

            # --- simulate fill (lots already determined from actual fill price) ---
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

            new_pos = OpenPosition(
                fill=fill,
                value_per_point_per_lot=value_per_point_per_lot,
                point=point,
                spread_points=config.spread_points,   # Fix 3: stored for SELL ask-side exits
            )
            counters["trades_opened"] += 1
            logger.info(
                "Opened %s %s fill=%.2f sl=%.2f tp=%.2f lots=%.2f",
                fill.setup_id, fill.direction, fill.fill_price,
                fill.sl_price, fill.tp_price, fill.lots,
            )

            # Fix 2: evaluate SL/TP on the fill candle itself
            entry_status, entry_close = new_pos.check_entry_candle(candle)
            if entry_status != PositionStatus.OPEN:
                exit_reason = "TP" if entry_status == PositionStatus.HIT_TP else "SL"
                commission = 2.0 * config.commission_usd * fill.lots
                closed = new_pos.close(entry_close, idx, candle.timestamp, exit_reason, commission)
                ledger.record(closed)
                equity += closed.net_pnl_usd
                equity_curve.append((candle.timestamp, equity))
                logger.info(
                    "Entry-candle %s for %s: gross=%.2f net=%.2f",
                    exit_reason, closed.setup_id, closed.pnl_usd, closed.net_pnl_usd,
                )
            else:
                open_positions.append(new_pos)

    # 3. Force-close remaining open positions at last candle close
    if m5_candles and open_positions:
        last_candle = m5_candles[-1]
        last_idx = len(m5_candles) - 1
        for pos in open_positions:
            # For SELL force-close: exit at ask (bid_close + spread)
            if pos.fill.direction == "SELL":
                force_close_price = last_candle.close + pos.spread_points * pos.point
            else:
                force_close_price = last_candle.close
            commission = 2.0 * config.commission_usd * pos.fill.lots
            closed = pos.close(force_close_price, last_idx, last_candle.timestamp, "FORCE_CLOSE", commission)
            ledger.record(closed)
            equity += closed.net_pnl_usd
            equity_curve.append((last_candle.timestamp, equity))
            counters["force_closed"] += 1
            logger.info("Force-closed %s net=%.2f", closed.setup_id, closed.net_pnl_usd)

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
