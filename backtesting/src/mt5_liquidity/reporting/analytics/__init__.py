"""Phase 2 time-based performance analytics — package orchestrator.

Builds the complete AnalyticsBundle for one BacktestResult:
duration buckets, hourly, session, daily/weekly/monthly performance, and
mark-to-market equity/drawdown — all computed from full-precision engine
output, with accounting reconciliation checks.

Nothing in this package mutates strategy or execution behaviour.
"""
from __future__ import annotations

from mt5_liquidity.backtest.engine import BacktestResult
from mt5_liquidity.config import BacktestConfig

from .duration import analyze_duration
from .equity import MtmPoint, analyze_mark_to_market
from .hourly import analyze_hours
from .models import (
    AnalyticsBundle,
    DrawdownSummary,
    Reconciliation,
    SessionWindow,
)
from .periods import analyze_periods
from .sessions import OVERLAP_NAME, OFF_SESSION_NAME, analyze_sessions, resolve_sessions

ATTRIBUTION_POLICY = (
    "Duration: per trade from fill_ts/close_ts. Hourly: entry hour (UTC "
    "canonical; display hour is a separate column; exit-hour analysis not "
    "included in Phase 2). Sessions: primary entry-session classification, "
    "mutually exclusive with precedence LONDON_NY_OVERLAP > LONDON > "
    "NEW_YORK > TOKYO > OFF_SESSION. Daily/weekly/monthly: realised "
    "cash-flow metrics by close_ts (exit date); entry-cohort columns "
    "(entry_trade_count, entry_net_pnl) by fill_ts. Weekly grouping uses "
    "ISO year + ISO week. All boundaries are UTC."
)

_TOLERANCE = 0.05  # USD tolerance for float reconciliation after rounding


def build_analytics_bundle(
    result: BacktestResult,
    config: BacktestConfig,
) -> AnalyticsBundle:
    """Compute every Phase 2 analytics view for one backtest result."""
    trades = result.closed_positions
    mtm_points: list[MtmPoint] = [
        (p.timestamp, p.realised_equity, p.floating_pnl, p.mtm_equity, p.open_positions)
        for p in result.mark_to_market_curve
    ]
    mtm_series = [(ts, mtm) for ts, _r, _f, mtm, _n in mtm_points]

    sessions = resolve_sessions(config.session_overrides)
    display_tz = config.analytics_display_timezone

    duration = analyze_duration(trades, config.duration_bucket_minutes)
    hourly = analyze_hours(trades, display_tz)
    session_rows = analyze_sessions(trades, sessions)
    daily, weekly, monthly, streaks = analyze_periods(trades, mtm_series)
    mtm_drawdown = analyze_mark_to_market(mtm_points)

    bundle = AnalyticsBundle(
        schema_version=1,
        timezone_policy="UTC",
        display_timezone=display_tz,
        sessions=sessions,
        attribution_policy=ATTRIBUTION_POLICY,
        duration=duration,
        hourly=hourly,
        session_rows=session_rows,
        daily=daily,
        weekly=weekly,
        monthly=monthly,
        period_streaks=streaks,
        mtm_drawdown=mtm_drawdown,
        mtm_points=mtm_points,
    )
    bundle.reconciliation = _reconcile(result, bundle)
    bundle.headline = _headline(bundle)
    return bundle


def _reconcile(result: BacktestResult, bundle: AnalyticsBundle) -> Reconciliation:
    """Verify aggregation counts and P&L sums against the trade ledger.

    Computed from full-precision data; rounding differences within
    _TOLERANCE are accepted. When no trades exist every count check is
    trivially satisfied.
    """
    stats = result.stats
    total = stats.total_trades

    hourly_sum = sum(r.stats.trade_count for r in bundle.hourly)
    session_sum = sum(r.stats.trade_count for r in bundle.session_rows)
    duration_sum = sum(b.stats.trade_count for b in bundle.duration.buckets)
    daily_exit_sum = sum(r.trade_count for r in bundle.daily)
    daily_entry_sum = sum(r.entry_trade_count for r in bundle.daily)

    daily_net = round(sum(r.net_pnl for r in bundle.daily), 2)
    weekly_net = round(sum(r.net_pnl for r in bundle.weekly), 2)
    monthly_net = round(sum(r.net_pnl for r in bundle.monthly), 2)
    hourly_gross_profit = round(sum(r.stats.gross_profit for r in bundle.hourly), 2)
    hourly_gross_loss = round(sum(r.stats.gross_loss for r in bundle.hourly), 2)

    final_mtm = bundle.mtm_points[-1][3] if bundle.mtm_points else None

    counts_ok = (
        hourly_sum == total
        and session_sum == total
        and duration_sum == total
        and daily_exit_sum == total
        and daily_entry_sum == total
    )
    net_ok = (
        abs(daily_net - stats.net_pnl) <= _TOLERANCE
        and abs(weekly_net - stats.net_pnl) <= _TOLERANCE
        and abs(monthly_net - stats.net_pnl) <= _TOLERANCE
    )
    gp_ok = abs(hourly_gross_profit - stats.gross_profit) <= _TOLERANCE
    gl_ok = abs(hourly_gross_loss - stats.gross_loss) <= _TOLERANCE
    # Final MTM equity reconciles with the ledger when the run had candles
    # (the engine appends a zero-floating final observation after force-closes).
    equity_ok = (
        final_mtm is None
        or abs(final_mtm - stats.final_equity) <= _TOLERANCE
    )

    return Reconciliation(
        total_trades=total,
        hourly_count_sum=hourly_sum,
        session_count_sum=session_sum,
        duration_count_sum=duration_sum,
        daily_exit_count_sum=daily_exit_sum,
        daily_entry_count_sum=daily_entry_sum,
        ledger_net_pnl=round(stats.net_pnl, 2),
        daily_net_pnl_sum=daily_net,
        weekly_net_pnl_sum=weekly_net,
        monthly_net_pnl_sum=monthly_net,
        ledger_gross_profit=round(stats.gross_profit, 2),
        hourly_gross_profit_sum=hourly_gross_profit,
        ledger_gross_loss=round(stats.gross_loss, 2),
        hourly_gross_loss_sum=hourly_gross_loss,
        final_mtm_equity=round(final_mtm, 2) if final_mtm is not None else None,
        ledger_final_equity=round(stats.final_equity, 2),
        counts_reconciled=counts_ok,
        net_pnl_reconciled=net_ok,
        gross_profit_reconciled=gp_ok,
        gross_loss_reconciled=gl_ok,
        final_equity_reconciled=equity_ok,
    )


def _headline(bundle: AnalyticsBundle) -> dict:
    """Concise highlights used by the console report and summary JSON."""
    headline: dict = {}

    traded_hours = [r for r in bundle.hourly if r.stats.trade_count > 0]
    if traded_hours:
        best = max(traded_hours, key=lambda r: r.stats.net_pnl)
        worst = min(traded_hours, key=lambda r: r.stats.net_pnl)
        headline["best_hour_utc"] = best.hour_utc
        headline["best_hour_net_pnl"] = round(best.stats.net_pnl, 2)
        headline["worst_hour_utc"] = worst.hour_utc
        headline["worst_hour_net_pnl"] = round(worst.stats.net_pnl, 2)

    traded_sessions = [r for r in bundle.session_rows if r.stats.trade_count > 0]
    if traded_sessions:
        best = max(traded_sessions, key=lambda r: r.stats.net_pnl)
        worst = min(traded_sessions, key=lambda r: r.stats.net_pnl)
        headline["best_session"] = best.session
        headline["best_session_net_pnl"] = round(best.stats.net_pnl, 2)
        headline["worst_session"] = worst.session
        headline["worst_session_net_pnl"] = round(worst.stats.net_pnl, 2)

    if bundle.daily:
        profitable = sum(1 for r in bundle.daily if r.net_pnl > 0)
        losing = sum(1 for r in bundle.daily if r.net_pnl < 0)
        headline["profitable_days"] = profitable
        headline["losing_days"] = losing
        headline["profitable_day_pct"] = round(
            profitable / len(bundle.daily) * 100.0, 2)

    d = bundle.duration
    if d and d.avg_duration_minutes is not None:
        headline["avg_duration_minutes"] = round(d.avg_duration_minutes, 1)
        headline["median_duration_minutes"] = round(d.median_duration_minutes, 1)
        headline["same_candle_closes"] = d.same_candle_closes

    dd: DrawdownSummary = bundle.mtm_drawdown
    headline["mtm_max_drawdown_usd"] = round(dd.max_drawdown_usd, 2)
    headline["mtm_max_drawdown_pct"] = round(dd.max_drawdown_pct, 4)
    return headline


__all__ = [
    "ATTRIBUTION_POLICY",
    "AnalyticsBundle",
    "OVERLAP_NAME",
    "OFF_SESSION_NAME",
    "SessionWindow",
    "build_analytics_bundle",
]
