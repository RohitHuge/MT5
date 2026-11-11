"""Backtest engine: wire strategy signals into fills, positions, and ledger.

No MT5 imports. No order_send. No live trading.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from ..config import BacktestConfig
from ..data.candles import Candle
from ..strategy.liquidity_sweep import run_liquidity_sweep_strategy
from ..strategy.setup import Setup, SetupState
from .execution import FillResult, compute_fill_price, simulate_fill
from .ledger import LedgerStats, TradeLedger
from .positions import ClosedPosition, OpenPosition, PositionStatus
from ..risk.position_sizing import SizingResult, calculate_position_size
from ..risk.account_risk import RISK_EXIT_REASONS, RiskEvent, build_account_risk_manager

logger = logging.getLogger(__name__)


@dataclass
class SymbolMeta:
    """Minimal symbol metadata needed by the engine."""
    point: float
    tick_value: float
    tick_size: float
    volume_min: float
    volume_max: float
    volume_step: float


def validate_symbol_meta(meta: SymbolMeta, symbol: str = "") -> str | None:
    """Return an error when symbol metadata cannot safely support calculations."""
    label = f"{symbol} " if symbol else ""
    for name in ("point", "tick_size", "tick_value", "volume_min", "volume_step", "volume_max"):
        value = getattr(meta, name)
        if value <= 0:
            return f"{label}{name}={value} must be > 0"
    if meta.volume_min > meta.volume_max:
        return f"{label}volume_min {meta.volume_min} > volume_max {meta.volume_max}"
    if meta.volume_step > meta.volume_max:
        return f"{label}volume_step {meta.volume_step} > volume_max {meta.volume_max}"
    return None


@dataclass
class MarkToMarketPoint:
    timestamp: datetime
    realised_equity: float
    floating_pnl: float
    mtm_equity: float
    open_positions: int


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
    equity_curve: list[tuple[datetime, float]]
    rejected_sizings: list[SizingResult]
    mark_to_market_curve: list[MarkToMarketPoint] = field(default_factory=list)
    # Phase 3: account-risk outputs (defaulted for backward compatibility).
    setups_skipped_risk: int = 0
    risk_blocked_by_reason: dict[str, int] = field(default_factory=dict)
    risk_events: list[RiskEvent] = field(default_factory=list)
    risk_summary: dict = field(default_factory=dict)


def run_backtest(
    m15_candles: list[Candle],
    m5_candles: list[Candle],
    config: BacktestConfig,
    symbol_meta: SymbolMeta,
) -> BacktestResult:
    """Run a historical backtest. Raises ValueError for invalid symbol metadata."""
    meta_error = validate_symbol_meta(symbol_meta)
    if meta_error:
        raise ValueError(f"Invalid symbol metadata: {meta_error}")

    point = symbol_meta.point
    tick_val = symbol_meta.tick_value
    tick_size = symbol_meta.tick_size
    value_per_point_per_lot = (tick_val / tick_size) * point

    all_setups: list[Setup] = run_liquidity_sweep_strategy(
        m15_candles=m15_candles, m5_candles=m5_candles, config=config, point=point,
    )
    entry_setups = [s for s in all_setups if s.state == SetupState.ENTRY_READY]
    unique_setups: dict[str, Setup] = {}
    for setup in sorted(entry_setups, key=lambda s: (s.retest.rejection_candle_index, s.setup_id)):
        unique_setups.setdefault(setup.setup_id, setup)
    entry_setups = sorted(
        unique_setups.values(),
        key=lambda s: (s.retest.rejection_candle_index, s.setup_id),
    )

    fill_bar_map: dict[int, list[Setup]] = {}
    for setup in entry_setups:
        fill_bar_map.setdefault(setup.retest.rejection_candle_index + 1, []).append(setup)

    initial_equity = config.initial_equity
    equity = initial_equity
    ledger = TradeLedger(initial_equity=initial_equity)
    open_positions: list[OpenPosition] = []
    equity_curve: list[tuple[datetime, float]] = []
    mark_to_market_curve: list[MarkToMarketPoint] = []
    day_start_equity: dict[date, float] = {}

    # Phase 3: one account-risk manager per run. When disabled the engine
    # keeps its legacy inline daily-loss guard and the legacy behaviour is
    # bit-for-bit unchanged.
    risk = build_account_risk_manager(config)

    counters = {
        "skipped_max_pos": 0, "skipped_daily_loss": 0, "skipped_sizing": 0,
        "skipped_no_next_bar": 0, "trades_opened": 0, "force_closed": 0,
        "skipped_risk": 0, "risk_force_closed": 0,
    }
    rejected_sizings: list[SizingResult] = []
    candle_entry_consumed: set[int] = set()

    for idx, candle in enumerate(m5_candles):
        # Candles are required to be UTC-aware by Candle; normalize explicitly
        # so a non-UTC aware input cannot silently reset the risk day locally.
        candle_utc = candle.timestamp.astimezone(timezone.utc)
        candle_date = candle_utc.date()

        # Phase 3: evaluate account risk against current mark-to-market equity
        # BEFORE normal SL/TP bookkeeping. In OHLC mode this is still a
        # candle-close observation (not tick-level protection), but it prevents
        # a just-closed stop from being followed by a second entry before the
        # daily lock is applied.
        if risk is not None:
            floating_before_exits = sum(pos.unrealized_pnl(candle) for pos in open_positions)
            risk.on_candle(
                candle.timestamp, realised_equity=equity,
                floating_pnl=floating_before_exits, open_positions=len(open_positions))

        # Resolve ordinary SL/TP exits. A risk CLOSE_ALL lock takes precedence:
        # positions are closed at this bar's executable close and recorded with
        # a risk-specific exit reason rather than being booked as an SL/TP.
        if risk is not None and risk.close_all_requested() and open_positions:
            exit_reason = RISK_EXIT_REASONS[risk.state]
            risk_closed: list[ClosedPosition] = []
            for pos in open_positions:
                commission = 2.0 * config.commission_usd * pos.fill.lots
                closed = pos.close(
                    pos.executable_exit_price(candle.close), idx,
                    candle.timestamp, exit_reason, commission)
                ledger.record(closed)
                equity += closed.net_pnl_usd
                equity_curve.append((candle.timestamp, equity))
                risk_closed.append(closed)
                counters["risk_force_closed"] += 1
            risk.record_forced_closures(candle.timestamp, risk_closed, equity)
            open_positions = []
        else:
            still_open: list[OpenPosition] = []
            for pos in open_positions:
                status, close_price = pos.check_candle(candle, idx)
                if status == PositionStatus.OPEN:
                    still_open.append(pos)
                else:
                    reason = "TP" if status == PositionStatus.HIT_TP else "SL"
                    commission = 2.0 * config.commission_usd * pos.fill.lots
                    closed = pos.close(close_price, idx, candle.timestamp, reason, commission)
                    ledger.record(closed)
                    equity += closed.net_pnl_usd
                    equity_curve.append((candle.timestamp, equity))
                    if risk is not None:
                        risk.on_trade_closed(
                            candle.timestamp, closed.net_pnl_usd, realised_equity=equity)
            open_positions = still_open

        # Legacy path only: a new UTC day baseline is the account's
        # mark-to-market equity at this first available candle. When the risk
        # manager is enabled it owns the daily baseline instead.
        if risk is None and candle_date not in day_start_equity:
            opening_floating = sum(pos.unrealized_pnl(candle) for pos in open_positions)
            day_start_equity[candle_date] = equity + opening_floating

        for setup in fill_bar_map.get(idx, []):
            if idx in candle_entry_consumed:
                counters["skipped_max_pos"] += 1
                continue
            if len(open_positions) >= config.max_positions:
                counters["skipped_max_pos"] += 1
                continue

            unrealised = sum(pos.unrealized_pnl(candle) for pos in open_positions)
            if risk is not None:
                # Phase 3: every new trade passes the account-risk gate.
                decision = risk.evaluate_entry(
                    candle.timestamp, realised_equity=equity, floating_pnl=unrealised)
                if not decision.allowed:
                    counters["skipped_risk"] += 1
                    logger.debug(
                        "Blocked %s — %s (state=%s)", setup.setup_id,
                        decision.reason_code, decision.state.value)
                    continue
            else:
                # Legacy inline daily-loss guard (unchanged behaviour).
                day_baseline = day_start_equity[candle_date]
                current_equity = equity + unrealised
                daily_loss_usd = current_equity - day_baseline
                if daily_loss_usd < 0 and day_baseline > 0:
                    daily_loss_pct = abs(daily_loss_usd) / day_baseline * 100.0
                    if daily_loss_pct >= config.max_daily_loss_pct:
                        counters["skipped_daily_loss"] += 1
                        continue

            fill_candle = candle
            actual_fill_price = compute_fill_price(
                setup.direction, fill_candle.open, config.spread_points,
                config.slippage_points, point,
            )
            invalid_stop_side = (
                (setup.direction == "BUY" and setup.stop_loss_price >= actual_fill_price)
                or (setup.direction == "SELL" and setup.stop_loss_price <= actual_fill_price)
            )
            if invalid_stop_side:
                rejected_sizings.append(SizingResult(
                    lots=0.0,
                    risk_amount=equity * config.risk_pct / 100.0,
                    sl_points=abs(actual_fill_price - setup.stop_loss_price) / point,
                    value_per_point_per_lot=value_per_point_per_lot,
                    rejection_reason="Stop-loss is on the wrong side of executable fill (gap through stop)",
                ))
                counters["skipped_sizing"] += 1
                continue

            sizing = calculate_position_size(
                signal_price=actual_fill_price, stop_loss_price=setup.stop_loss_price,
                equity=equity, risk_pct=config.risk_pct,
                volume_min=symbol_meta.volume_min, volume_max=symbol_meta.volume_max,
                volume_step=symbol_meta.volume_step, tick_value=tick_val,
                tick_size=tick_size, point=point,
                min_stop_distance_points=config.min_stop_distance_points,
                max_lots_per_trade=config.max_lots_per_trade,
            )
            if sizing.rejection_reason is not None:
                counters["skipped_sizing"] += 1
                rejected_sizings.append(sizing)
                continue

            if risk is not None:
                # Phase 3: per-trade monetary risk ceiling on the sized trade.
                # A violation is a trade-level rejection, not an account lock.
                actual_risk_usd = (sizing.lots * sizing.sl_points
                                   * sizing.value_per_point_per_lot)
                trade_gate = risk.check_trade_risk(
                    candle.timestamp, proposed_risk_usd=actual_risk_usd,
                    current_equity=equity + unrealised)
                daily_budget_gate = risk.check_daily_risk_budget(
                    candle.timestamp, proposed_risk_usd=actual_risk_usd,
                    realised_equity=equity, floating_pnl=unrealised)
                if not trade_gate.allowed or not daily_budget_gate.allowed:
                    counters["skipped_risk"] += 1
                    rejection = trade_gate if not trade_gate.allowed else daily_budget_gate
                    logger.debug(
                        "Blocked %s — %s (risk %.2f > %.2f)", setup.setup_id,
                        rejection.reason_code, actual_risk_usd,
                        rejection.threshold_value or 0.0)
                    continue

            fill = simulate_fill(
                setup_id=setup.setup_id, direction=setup.direction,
                signal_candle_index=setup.retest.rejection_candle_index,
                sl_price=setup.stop_loss_price, lots=sizing.lots,
                m5_candles=m5_candles, spread_points=config.spread_points,
                slippage_points=config.slippage_points, rr_target=config.rr_target,
                point=point,
            )
            if fill is None:
                counters["skipped_no_next_bar"] += 1
                continue

            position = OpenPosition(
                fill=fill, value_per_point_per_lot=value_per_point_per_lot,
                point=point, spread_points=config.spread_points,
                exit_slippage_points=config.exit_slippage_points,
            )
            counters["trades_opened"] += 1
            candle_entry_consumed.add(idx)
            entry_status, entry_close = position.check_entry_candle(candle)
            if entry_status != PositionStatus.OPEN:
                reason = "TP" if entry_status == PositionStatus.HIT_TP else "SL"
                commission = 2.0 * config.commission_usd * fill.lots
                closed = position.close(entry_close, idx, candle.timestamp, reason, commission)
                ledger.record(closed)
                equity += closed.net_pnl_usd
                equity_curve.append((candle.timestamp, equity))
                if risk is not None:
                    risk.on_trade_closed(
                        candle.timestamp, closed.net_pnl_usd, realised_equity=equity)
            else:
                open_positions.append(position)

        floating = sum(pos.unrealized_pnl(candle) for pos in open_positions)
        mark_to_market_curve.append(MarkToMarketPoint(
            timestamp=candle.timestamp, realised_equity=equity, floating_pnl=floating,
            mtm_equity=equity + floating, open_positions=len(open_positions),
        ))

    if m5_candles and open_positions:
        last = m5_candles[-1]
        last_idx = len(m5_candles) - 1
        for pos in open_positions:
            force_price = pos.executable_exit_price(last.close)
            commission = 2.0 * config.commission_usd * pos.fill.lots
            closed = pos.close(force_price, last_idx, last.timestamp, "FORCE_CLOSE", commission)
            ledger.record(closed)
            equity += closed.net_pnl_usd
            equity_curve.append((last.timestamp, equity))
            counters["force_closed"] += 1
        mark_to_market_curve.append(MarkToMarketPoint(
            timestamp=last.timestamp, realised_equity=equity, floating_pnl=0.0,
            mtm_equity=equity, open_positions=0,
        ))

    stats = ledger.compute_stats()
    return BacktestResult(
        setups_total=len(all_setups), setups_entry_ready=len(entry_setups),
        setups_skipped_max_pos=counters["skipped_max_pos"],
        setups_skipped_daily_loss=counters["skipped_daily_loss"],
        setups_skipped_sizing=counters["skipped_sizing"],
        setups_skipped_no_next_bar=counters["skipped_no_next_bar"],
        trades_opened=counters["trades_opened"], closed_positions=ledger.trades,
        force_closed=counters["force_closed"], stats=stats,
        equity_curve=equity_curve, rejected_sizings=rejected_sizings,
        mark_to_market_curve=mark_to_market_curve,
        setups_skipped_risk=counters["skipped_risk"],
        risk_blocked_by_reason=dict(risk.blocked_by_reason) if risk else {},
        risk_events=list(risk.events) if risk else [],
        risk_summary=risk.summary() if risk else {"enabled": False},
    )
