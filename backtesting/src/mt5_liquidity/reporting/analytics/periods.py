"""Daily, weekly (ISO), and monthly performance analytics (plan section 2F).

Attribution convention (documented):
- Realised cash-flow metrics (trade_count, wins/losses, net P&L, ...) are
  attributed by the trade's close_ts (exit date). A trade crossing midnight,
  a week, or a month boundary lands in the period where its P&L was realised.
- Entry-cohort columns (entry_trade_count, entry_net_pnl) are attributed by
  fill_ts and reported as separate columns — never mixed with exit figures.
- Weekly grouping uses ISO week (ISO year + ISO week) to avoid year-boundary
  ambiguity.
- Period boundaries are UTC.
- Equity open/close/high/low/return and per-period drawdown come from the
  end-of-candle mark-to-market equity curve; open equity carries forward
  through periods that contain no observations (weekends, holidays).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from mt5_liquidity.backtest.positions import ClosedPosition

from .aggregation import aggregate_trade_stats, max_drawdown_from_series, period_streaks
from .models import PeriodRow, PeriodStreaks

UTC = ZoneInfo("UTC")


def _day_key(ts: datetime) -> tuple[str, date]:
    d = ts.astimezone(UTC).date()
    return d.isoformat(), d


def _week_key(ts: datetime) -> tuple[str, date]:
    d = ts.astimezone(UTC).date()
    iso = d.isocalendar()
    # ISO weekday is 1-based (Mon=1..Sun=7): step back weekday-1 days to the
    # Monday that anchors this ISO week.
    monday = d - timedelta(days=iso.weekday - 1)
    return f"{iso.year}-W{iso.week:02d}", monday


def _month_key(ts: datetime) -> tuple[str, date]:
    d = ts.astimezone(UTC).date()
    return f"{d.year}-{d.month:02d}", d.replace(day=1)


def _iterate_periods(
    start: datetime, end: datetime, granularity: str
) -> list[tuple[str, datetime, datetime]]:
    """Enumerate [period_id, period_start, period_end) covering [start, end]."""
    if end < start:
        return []
    periods: list[tuple[str, datetime, datetime]] = []
    if granularity == "daily":
        cursor = start.astimezone(UTC).date()
        final = end.astimezone(UTC).date()
        while cursor <= final:
            nxt = cursor + timedelta(days=1)
            periods.append((cursor.isoformat(),
                            datetime.combine(cursor, time.min, tzinfo=UTC),
                            datetime.combine(nxt, time.min, tzinfo=UTC)))
            cursor = nxt
    elif granularity == "weekly":
        _, anchor = _week_key(start)
        _, final_anchor = _week_key(end)
        while anchor <= final_anchor:
            nxt = anchor + timedelta(days=7)
            iso = anchor.isocalendar()
            periods.append((f"{iso.year}-W{iso.week:02d}",
                            datetime.combine(anchor, time.min, tzinfo=UTC),
                            datetime.combine(nxt, time.min, tzinfo=UTC)))
            anchor = nxt
    elif granularity == "monthly":
        cursor = start.astimezone(UTC).date().replace(day=1)
        final = end.astimezone(UTC).date().replace(day=1)
        while cursor <= final:
            if cursor.month == 12:
                nxt = cursor.replace(year=cursor.year + 1, month=1)
            else:
                nxt = cursor.replace(month=cursor.month + 1)
            periods.append((f"{cursor.year}-{cursor.month:02d}",
                            datetime.combine(cursor, time.min, tzinfo=UTC),
                            datetime.combine(nxt, time.min, tzinfo=UTC)))
            cursor = nxt
    else:
        raise ValueError(f"Unknown period granularity: {granularity}")
    return periods


def _equity_for_period(
    period_start: datetime,
    period_end: datetime,
    mtm_points: list[tuple[datetime, float]],
) -> tuple[float | None, float | None, float | None, float | None,
           float | None, float | None]:
    """Equity open/close/high/low/return/max_drawdown for one period.

    open carries the last observation at or before period_start forward; a
    period with no in-period observations inherits open as its close/high/low.
    """
    prior = [v for ts, v in mtm_points if ts < period_start]
    inside = [(ts, v) for ts, v in mtm_points if period_start <= ts < period_end]

    if not prior and not inside:
        return None, None, None, None, None, None

    open_equity = prior[-1] if prior else inside[0][1]
    close_equity = inside[-1][1] if inside else open_equity
    high = max((v for _, v in inside), default=open_equity)
    low = min((v for _, v in inside), default=open_equity)
    return_pct = ((close_equity - open_equity) / open_equity * 100.0
                  if open_equity else None)
    series = ([(period_start, open_equity)] if prior else []) + inside
    max_dd = max_drawdown_from_series(series).max_drawdown_usd
    return open_equity, close_equity, high, low, return_pct, max_dd


def build_period_rows(
    trades: list[ClosedPosition],
    mtm_points: list[tuple[datetime, float]],
    granularity: str,
) -> list[PeriodRow]:
    """Build ordered PeriodRow list for one granularity, including zero-trade
    periods across the full observation range."""
    if granularity not in ("daily", "weekly", "monthly"):
        raise ValueError(f"Unknown period granularity: {granularity}")
    if not trades and not mtm_points:
        return []

    # The enumeration range must cover every trade timestamp AND every
    # equity observation so no trade is ever dropped from its exit period.
    candidates_start: list[datetime] = []
    candidates_end: list[datetime] = []
    if mtm_points:
        candidates_start.append(mtm_points[0][0])
        candidates_end.append(mtm_points[-1][0])
    if trades:
        candidates_start.append(min(t.fill_ts for t in trades))
        candidates_end.append(max(t.close_ts for t in trades))
    range_start = min(candidates_start)
    range_end = max(candidates_end)

    key_fn = {"daily": _day_key, "weekly": _week_key, "monthly": _month_key}[granularity]

    exit_groups: dict[str, list[ClosedPosition]] = {}
    entry_groups: dict[str, list[ClosedPosition]] = {}
    for trade in trades:
        exit_id, _ = key_fn(trade.close_ts)
        entry_id, _ = key_fn(trade.fill_ts)
        exit_groups.setdefault(exit_id, []).append(trade)
        entry_groups.setdefault(entry_id, []).append(trade)

    rows: list[PeriodRow] = []
    for period_id, p_start, p_end in _iterate_periods(range_start, range_end, granularity):
        closed = exit_groups.get(period_id, [])
        entered = entry_groups.get(period_id, [])
        stats = aggregate_trade_stats(closed)
        entry_net = (
            sum(t.net_pnl_usd for t in entered) if entered else 0.0
        )
        eq_open, eq_close, eq_high, eq_low, ret, max_dd = _equity_for_period(
            p_start, p_end, mtm_points)

        rows.append(PeriodRow(
            period_id=period_id,
            trade_count=stats.trade_count,
            winners=stats.winners,
            losers=stats.losers,
            breakeven=stats.breakeven,
            win_rate=round(stats.win_rate, 4),
            gross_profit=round(stats.gross_profit, 2),
            gross_loss=round(stats.gross_loss, 2),
            net_pnl=round(stats.net_pnl, 2),
            total_commission=round(stats.total_commission, 2),
            avg_net_pnl=round(stats.avg_net_pnl, 2),
            profit_factor=(stats.profit_factor if stats.profit_factor == float("inf")
                           else round(stats.profit_factor, 4)),
            entry_trade_count=len(entered),
            entry_net_pnl=round(entry_net, 2),
            equity_open=round(eq_open, 2) if eq_open is not None else None,
            equity_close=round(eq_close, 2) if eq_close is not None else None,
            equity_high=round(eq_high, 2) if eq_high is not None else None,
            equity_low=round(eq_low, 2) if eq_low is not None else None,
            return_pct=round(ret, 4) if ret is not None else None,
            max_drawdown_usd=round(max_dd, 2) if max_dd is not None else None,
        ))
    return rows


def analyze_periods(
    trades: list[ClosedPosition],
    mtm_points: list[tuple[datetime, float]],
) -> tuple[list[PeriodRow], list[PeriodRow], list[PeriodRow], dict[str, PeriodStreaks]]:
    """Build daily, weekly, and monthly rows plus period-level streaks."""
    daily = build_period_rows(trades, mtm_points, "daily")
    weekly = build_period_rows(trades, mtm_points, "weekly")
    monthly = build_period_rows(trades, mtm_points, "monthly")

    streaks = {
        "daily": _streaks_for(daily),
        "weekly": _streaks_for(weekly),
        "monthly": _streaks_for(monthly),
    }
    return daily, weekly, monthly, streaks


def _streaks_for(rows: list[PeriodRow]) -> PeriodStreaks:
    max_p, max_l, n_p, n_l, n_flat = period_streaks([r.net_pnl for r in rows])
    return PeriodStreaks(
        max_profitable_periods=max_p,
        max_losing_periods=max_l,
        profitable_periods=n_p,
        losing_periods=n_l,
        flat_periods=n_flat,
    )
