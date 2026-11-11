"""Shared, reusable analytics calculations used by every Phase 2 module.

All functions take full-precision trade/equity inputs (never rounded report
values) and keep full precision internally. Rounding happens only at export.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from mt5_liquidity.backtest.positions import ClosedPosition

from .models import DrawdownSummary, TradeGroupStats


def aggregate_trade_stats(trades: list[ClosedPosition]) -> TradeGroupStats:
    """Aggregate a group of closed trades into TradeGroupStats.

    Winners: net_pnl_usd > 0. Losers: net_pnl_usd <= 0 (ledger convention).
    breakeven counts the net == 0 subset of losers so wins + losers == total.
    """
    count = len(trades)
    if count == 0:
        return TradeGroupStats()

    winners = [t for t in trades if t.net_pnl_usd > 0]
    losers = [t for t in trades if t.net_pnl_usd <= 0]
    breakeven = [t for t in trades if t.net_pnl_usd == 0]

    gross_profit = sum(t.pnl_usd for t in trades if t.pnl_usd > 0)
    gross_loss = sum(t.pnl_usd for t in trades if t.pnl_usd <= 0)
    total_commission = sum(t.commission_usd for t in trades)
    net_pnl = gross_profit + gross_loss - total_commission

    profit_factor = (
        gross_profit / abs(gross_loss) if gross_loss != 0 else float("inf")
    )

    return TradeGroupStats(
        trade_count=count,
        winners=len(winners),
        losers=len(losers),
        breakeven=len(breakeven),
        win_rate=len(winners) / count,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        net_pnl=net_pnl,
        total_commission=total_commission,
        avg_net_pnl=net_pnl / count,
        avg_win=(sum(t.net_pnl_usd for t in winners) / len(winners)) if winners else 0.0,
        avg_loss=(sum(t.net_pnl_usd for t in losers) / len(losers)) if losers else 0.0,
        profit_factor=profit_factor,
    )


def max_drawdown_from_series(
    points: list[tuple[datetime, float]],
) -> DrawdownSummary:
    """Peak/trough/max-drawdown over a chronological (timestamp, equity) series.

    - Peak is the highest equity seen so far; drawdown at a point is peak - equity.
    - Trough is the lowest equity between the max-drawdown peak and recovery.
    - recovery_ts is the first timestamp at or after the trough where equity
      returns to the peak level; underwater_minutes runs peak_ts -> recovery_ts
      (or -> last point if never recovered).
    - Empty series returns an empty DrawdownSummary.
    """
    if not points:
        return DrawdownSummary()

    peak = points[0][1]
    peak_ts = points[0][0]
    summary = DrawdownSummary(peak_equity=peak, peak_ts=peak_ts)
    max_dd = 0.0

    candidate_trough: float | None = None
    candidate_trough_ts: datetime | None = None

    for ts, value in points:
        if value > peak:
            peak = value
            peak_ts = ts
            candidate_trough = None
            candidate_trough_ts = None
        dd = peak - value
        if dd > max_dd:
            max_dd = dd
            summary.peak_equity = peak
            summary.peak_ts = peak_ts
            summary.trough_equity = value
            summary.trough_ts = ts
        if value < peak:
            if candidate_trough is None or value < candidate_trough:
                candidate_trough = value
                candidate_trough_ts = ts

    if max_dd > 0:
        summary.max_drawdown_usd = max_dd
        summary.max_drawdown_pct = max_dd / summary.peak_equity * 100.0 if summary.peak_equity else 0.0
        # Recovery: first point at/after trough returning to the peak level.
        for ts, value in points:
            if summary.trough_ts is not None and ts >= summary.trough_ts and value >= summary.peak_equity:
                summary.recovery_ts = ts
                break
        start = summary.peak_ts
        end = summary.recovery_ts or points[-1][0]
        if start is not None:
            summary.underwater_minutes = (end - start).total_seconds() / 60.0

    return summary


def period_streaks(net_pnl_by_period: list[float]) -> tuple[int, int, int, int, int]:
    """Period-level profitable/losing streaks from an ordered net P&L list.

    Returns (max_profitable, max_losing, profitable_count, losing_count,
    flat_count). Flat periods (net == 0) break both streaks.
    """
    max_profit = max_loss = cur_profit = cur_loss = 0
    profitable = losing = flat = 0
    for value in net_pnl_by_period:
        if value > 0:
            profitable += 1
            cur_profit += 1
            cur_loss = 0
        elif value < 0:
            losing += 1
            cur_loss += 1
            cur_profit = 0
        else:
            flat += 1
            cur_profit = 0
            cur_loss = 0
        max_profit = max(max_profit, cur_profit)
        max_loss = max(max_loss, cur_loss)
    return max_profit, max_loss, profitable, losing, flat


def trade_duration_minutes(trade: ClosedPosition) -> float:
    """Holding duration in minutes from fill_ts to close_ts.

    Raises ValueError when timestamps are missing or inverted — analytics
    must never silently absorb malformed trade records.
    """
    fill_ts = trade.fill_ts
    close_ts = trade.close_ts
    if not isinstance(fill_ts, datetime) or not isinstance(close_ts, datetime):
        raise ValueError(
            f"Trade {trade.setup_id} has non-datetime timestamps: "
            f"fill_ts={fill_ts!r}, close_ts={close_ts!r}"
        )
    if close_ts < fill_ts:
        raise ValueError(
            f"Trade {trade.setup_id} closes ({close_ts.isoformat()}) before it "
            f"fills ({fill_ts.isoformat()})"
        )
    return (close_ts - fill_ts).total_seconds() / 60.0


def median(values: list[float]) -> float | None:
    """Median of a list; None when empty (statistics.median equivalent)."""
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0
