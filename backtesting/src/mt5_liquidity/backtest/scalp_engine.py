"""EMA-crossover scalping backtest engine.

Reuses the same execution, position, ledger, and risk-manager infrastructure
as the liquidity-sweep engine.  The only difference is the signal source:
signals come from `detect_scalp_signals()` (EMA 9/21/50 crossovers) rather
than the multi-stage sweep → MSS → retest pipeline.

Returns the same `BacktestResult` dataclass, so all existing reporting and
export tools work unchanged.

No MT5 imports. No live trading.
"""
from __future__ import annotations

import logging
from datetime import date, timezone

from ..config import BacktestConfig
from ..data.candles import Candle
from ..strategy.scalp_ema import ScalpSignal, detect_scalp_signals
from .engine import BacktestResult, MarkToMarketPoint, SymbolMeta, validate_symbol_meta
from .execution import compute_fill_price, simulate_fill
from .ledger import TradeLedger
from .positions import ClosedPosition, OpenPosition, PositionStatus
from ..risk.position_sizing import SizingResult, calculate_position_size
from ..risk.account_risk import RISK_EXIT_REASONS, RiskEvent, build_account_risk_manager
from ..session_classifier import classify_session, resolve_sessions

logger = logging.getLogger(__name__)


def run_scalp_backtest(
    m5_candles: list[Candle],
    config: BacktestConfig,
    symbol_meta: SymbolMeta,
    sl_swing_bars: int = 4,
    sl_buffer_points: int = 15,
    rsi_min_buy: float = 55.0,
    rsi_max_sell: float = 45.0,
) -> BacktestResult:
    """Run an EMA-crossover scalp backtest on M5 data.

    Parameters
    ----------
    m5_candles:
        Full M5 price series (UTC-aware; fetched and validated upstream).
    config:
        Standard BacktestConfig — spread, risk_pct, rr_target, max_positions,
        skip_sessions, account_risk (daily_target_usd / daily_loss_usd), etc.
    symbol_meta:
        XAUUSD contract metadata (point, tick_value, …).
    sl_swing_bars:
        Bars to look back for the swing high/low that anchors the SL.
    sl_buffer_points:
        Extra points of buffer beyond the swing extreme.
    """
    meta_error = validate_symbol_meta(symbol_meta)
    if meta_error:
        raise ValueError(f"Invalid symbol metadata: {meta_error}")

    point      = symbol_meta.point
    tick_val   = symbol_meta.tick_value
    tick_size  = symbol_meta.tick_size
    value_per_point_per_lot = (tick_val / tick_size) * point

    # --- Signal detection (pre-compute all crossovers) ---
    all_signals: list[ScalpSignal] = detect_scalp_signals(
        candles=m5_candles,
        point=point,
        sl_swing_bars=sl_swing_bars,
        sl_buffer_points=sl_buffer_points,
        rsi_min_buy=rsi_min_buy,
        rsi_max_sell=rsi_max_sell,
    )

    # Map fill_bar_index → list of signals (fill on NEXT candle after signal)
    fill_bar_map: dict[int, list[ScalpSignal]] = {}
    for sig in all_signals:
        fill_idx = sig.signal_candle_index + 1
        fill_bar_map.setdefault(fill_idx, []).append(sig)

    # --- Simulation state ---
    initial_equity = config.initial_equity
    equity         = initial_equity
    ledger         = TradeLedger(initial_equity=initial_equity)
    open_positions: list[OpenPosition] = []
    equity_curve: list[tuple] = []
    mark_to_market_curve: list[MarkToMarketPoint] = []
    day_start_equity: dict[date, float] = {}

    swap_long      = config.swap_long_points
    swap_short     = config.swap_short_points
    swap_triple    = config.swap_triple_day
    _swap_active   = (swap_long != 0.0 or swap_short != 0.0)
    _prev_date: date | None = None

    _skip_sessions  = set(config.skip_sessions or [])
    _session_windows = resolve_sessions(config.session_overrides) if _skip_sessions else []

    risk = build_account_risk_manager(config)

    counters = {
        "skipped_max_pos": 0, "skipped_daily_loss": 0, "skipped_sizing": 0,
        "skipped_no_next_bar": 0, "trades_opened": 0, "force_closed": 0,
        "skipped_risk": 0, "risk_force_closed": 0, "skipped_session": 0,
    }
    rejected_sizings: list[SizingResult] = []

    def floating_net(candle: Candle) -> float:
        return sum(
            pos.unrealized_pnl(candle) - 2.0 * config.commission_usd * pos.fill.lots
            for pos in open_positions
        )

    # --- Main simulation loop ---
    for idx, candle in enumerate(m5_candles):
        candle_utc  = candle.timestamp.astimezone(timezone.utc)
        candle_date = candle_utc.date()

        # Overnight swap accrual at the UTC date boundary
        if _swap_active and _prev_date is not None and candle_date != _prev_date:
            triple     = (_prev_date.weekday() == swap_triple)
            multiplier = 3.0 if triple else 1.0
            for pos in open_positions:
                swap_pts = swap_long if pos.fill.direction == "BUY" else swap_short
                pos.accrue_swap(swap_pts, multiplier)
        _prev_date = candle_date

        # Risk-manager candle update (must run before SL/TP bookkeeping)
        if risk is not None:
            floating_now = floating_net(candle)
            risk.on_candle(
                candle.timestamp, realised_equity=equity,
                floating_pnl=floating_now, open_positions=len(open_positions))
            if risk.close_all_requested() and open_positions:
                exit_reason = RISK_EXIT_REASONS[risk.state]
                risk_closed: list[ClosedPosition] = []
                for pos in open_positions:
                    pos.observe_candle(candle)
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
                        reason     = "TP" if status == PositionStatus.HIT_TP else "SL"
                        commission = 2.0 * config.commission_usd * pos.fill.lots
                        closed     = pos.close(close_price, idx, candle.timestamp, reason, commission)
                        ledger.record(closed)
                        equity += closed.net_pnl_usd
                        equity_curve.append((candle.timestamp, equity))
                        risk.on_trade_closed(
                            candle.timestamp, closed.net_pnl_usd, realised_equity=equity)
                open_positions = still_open
        else:
            still_open = []
            for pos in open_positions:
                status, close_price = pos.check_candle(candle, idx)
                if status == PositionStatus.OPEN:
                    still_open.append(pos)
                else:
                    reason     = "TP" if status == PositionStatus.HIT_TP else "SL"
                    commission = 2.0 * config.commission_usd * pos.fill.lots
                    closed     = pos.close(close_price, idx, candle.timestamp, reason, commission)
                    ledger.record(closed)
                    equity += closed.net_pnl_usd
                    equity_curve.append((candle.timestamp, equity))
            open_positions = still_open

        if risk is None and candle_date not in day_start_equity:
            day_start_equity[candle_date] = equity + floating_net(candle)

        # Process signals whose fill bar is this candle
        for sig in fill_bar_map.get(idx, []):

            if len(open_positions) >= config.max_positions:
                counters["skipped_max_pos"] += 1
                continue

            # Session filter — block entry if the fill candle is in a blocked session
            if _skip_sessions:
                entry_session = classify_session(candle.timestamp, _session_windows)
                if entry_session in _skip_sessions:
                    counters["skipped_session"] += 1
                    continue

            unrealised = floating_net(candle)
            if risk is not None:
                decision = risk.evaluate_entry(
                    candle.timestamp, realised_equity=equity, floating_pnl=unrealised)
                if not decision.allowed:
                    counters["skipped_risk"] += 1
                    logger.debug("Blocked %s — %s", sig.signal_id, decision.reason_code)
                    continue
            else:
                day_baseline = day_start_equity[candle_date]
                current_eq   = equity + unrealised
                daily_loss   = current_eq - day_baseline
                if daily_loss < 0 and day_baseline > 0:
                    if abs(daily_loss) / day_baseline * 100.0 >= config.max_daily_loss_pct:
                        counters["skipped_daily_loss"] += 1
                        continue

            actual_fill_price = compute_fill_price(
                sig.direction, candle.open,
                config.spread_points, config.slippage_points, point,
            )

            # Reject if the computed SL is already on the wrong side of the fill
            invalid_stop = (
                (sig.direction == "BUY"  and sig.sl_price >= actual_fill_price) or
                (sig.direction == "SELL" and sig.sl_price <= actual_fill_price)
            )
            if invalid_stop:
                rejected_sizings.append(SizingResult(
                    lots=0.0,
                    risk_amount=equity * config.risk_pct / 100.0,
                    sl_points=abs(actual_fill_price - sig.sl_price) / point,
                    value_per_point_per_lot=value_per_point_per_lot,
                    rejection_reason="SL on wrong side of fill (gap through stop)",
                ))
                counters["skipped_sizing"] += 1
                continue

            sizing = calculate_position_size(
                signal_price=actual_fill_price,
                stop_loss_price=sig.sl_price,
                equity=equity + unrealised,
                risk_pct=config.risk_pct,
                volume_min=symbol_meta.volume_min,
                volume_max=symbol_meta.volume_max,
                volume_step=symbol_meta.volume_step,
                tick_value=tick_val,
                tick_size=tick_size,
                point=point,
                min_stop_distance_points=config.min_stop_distance_points,
                max_lots_per_trade=config.max_lots_per_trade,
            )
            if sizing.rejection_reason is not None:
                counters["skipped_sizing"] += 1
                rejected_sizings.append(sizing)
                continue

            fill = simulate_fill(
                setup_id=sig.signal_id,
                direction=sig.direction,
                signal_candle_index=sig.signal_candle_index,
                sl_price=sig.sl_price,
                lots=sizing.lots,
                m5_candles=m5_candles,
                spread_points=config.spread_points,
                slippage_points=config.slippage_points,
                rr_target=config.rr_target,
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

            if risk is not None:
                actual_risk_usd    = position.initial_stop_risk_usd(
                    2.0 * config.commission_usd * fill.lots)
                trade_gate         = risk.check_trade_risk(
                    candle.timestamp, proposed_risk_usd=actual_risk_usd,
                    current_equity=equity + unrealised)
                open_reserve_usd   = sum(
                    pos.additional_stop_risk_usd(
                        candle, 2.0 * config.commission_usd * pos.fill.lots)
                    for pos in open_positions)
                daily_budget_gate  = risk.check_daily_risk_budget(
                    ts=candle.timestamp, realised_equity=equity,
                    floating_pnl=unrealised,
                    open_position_reserve_usd=open_reserve_usd,
                    proposed_risk_usd=actual_risk_usd)
                if not trade_gate.allowed or not daily_budget_gate.allowed:
                    counters["skipped_risk"] += 1
                    continue

            counters["trades_opened"] += 1

            # Same-bar resolution: check if SL/TP is hit on the fill candle
            entry_status, entry_close = position.check_entry_candle(candle)
            if entry_status != PositionStatus.OPEN:
                reason     = "TP" if entry_status == PositionStatus.HIT_TP else "SL"
                commission = 2.0 * config.commission_usd * fill.lots
                closed     = position.close(entry_close, idx, candle.timestamp, reason, commission)
                ledger.record(closed)
                equity += closed.net_pnl_usd
                equity_curve.append((candle.timestamp, equity))
                if risk is not None:
                    risk.on_trade_closed(
                        candle.timestamp, closed.net_pnl_usd, realised_equity=equity)
            else:
                open_positions.append(position)

        floating = floating_net(candle)
        mark_to_market_curve.append(MarkToMarketPoint(
            timestamp=candle.timestamp, realised_equity=equity,
            floating_pnl=floating, mtm_equity=equity + floating,
            open_positions=len(open_positions),
        ))

    # Force-close any positions still open at end of data
    if m5_candles and open_positions:
        last     = m5_candles[-1]
        last_idx = len(m5_candles) - 1
        for pos in open_positions:
            force_price = pos.executable_exit_price(last.close)
            commission  = 2.0 * config.commission_usd * pos.fill.lots
            closed      = pos.close(force_price, last_idx, last.timestamp, "FORCE_CLOSE", commission)
            ledger.record(closed)
            equity += closed.net_pnl_usd
            equity_curve.append((last.timestamp, equity))
            counters["force_closed"] += 1
        mark_to_market_curve.append(MarkToMarketPoint(
            timestamp=last.timestamp, realised_equity=equity,
            floating_pnl=0.0, mtm_equity=equity, open_positions=0,
        ))

    stats = ledger.compute_stats()
    return BacktestResult(
        setups_total=len(all_signals),
        setups_entry_ready=len(all_signals),     # all signals are ENTRY_READY by definition
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
        mark_to_market_curve=mark_to_market_curve,
        setups_skipped_session=counters["skipped_session"],
        setups_skipped_risk=counters["skipped_risk"],
        risk_blocked_by_reason=dict(risk.blocked_by_reason) if risk else {},
        risk_events=list(risk.events) if risk else [],
        risk_summary=risk.summary() if risk else {"enabled": False},
    )
