"""Entry-hour performance analytics (plan section 2D).

Canonical grouping: entry hour 0-23 in UTC. An optional display hour in
config.analytics_display_timezone is included as a separate, clearly-labelled
column — it never replaces the UTC grouping. Exit-hour attribution is NOT
included in Phase 2 (documented decision: entry-hour and exit-hour metrics
must not be mixed).
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

from mt5_liquidity.backtest.positions import ClosedPosition

from .aggregation import aggregate_trade_stats
from .models import HourRow


def analyze_hours(
    trades: list[ClosedPosition],
    display_timezone: str = "UTC",
) -> list[HourRow]:
    """Group closed trades by the UTC hour of their fill timestamp.

    Returns exactly 24 rows (hours with no trades have zeroed stats) so the
    CSV always represents the full day. Raises ValueError for an unknown
    display timezone instead of silently falling back.
    """
    try:
        display_tz = ZoneInfo(display_timezone)
    except Exception as exc:
        raise ValueError(
            f"Unknown analytics_display_timezone {display_timezone!r}: {exc}"
        ) from exc

    by_hour: dict[int, list[ClosedPosition]] = {h: [] for h in range(24)}
    for trade in trades:
        by_hour[trade.fill_ts.astimezone(ZoneInfo("UTC")).hour].append(trade)

    rows: list[HourRow] = []
    for hour in range(24):
        group = by_hour[hour]
        stats = aggregate_trade_stats(group)
        drawdown_contribution = sum(
            t.net_pnl_usd for t in group if t.net_pnl_usd <= 0
        )
        # Reference instant inside the UTC hour; convert for the display hour.
        reference = f"2026-01-15T{hour:02d}:00:00+00:00"
        from datetime import datetime

        local_hour = datetime.fromisoformat(reference).astimezone(display_tz).hour
        rows.append(HourRow(
            hour_utc=hour,
            hour_display=local_hour,
            display_timezone=display_timezone,
            stats=stats,
            drawdown_contribution=drawdown_contribution,
        ))
    return rows
