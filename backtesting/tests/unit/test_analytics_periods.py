"""Unit tests for daily/weekly/monthly analytics (reporting/analytics/periods.py)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mt5_liquidity.backtest.positions import ClosedPosition
from mt5_liquidity.reporting.analytics.periods import (
    analyze_periods,
    build_period_rows,
)

UTC = timezone.utc
# Friday 2026-11-06 .. Monday 2026-11-09 span a weekend.
FRIDAY = datetime(2026, 11, 6, 10, 0, tzinfo=UTC)


def _trade(fill: datetime, close: datetime, pnl: float) -> ClosedPosition:
    return ClosedPosition(
        setup_id=f"P{fill.isoformat()}", direction="BUY", fill_price=2000.0,
        close_price=2001.0, tp_price=2004.0, sl_price=1998.0,
        lots=0.1, pnl_usd=pnl, exit_reason="TP" if pnl > 0 else "SL",
        fill_candle_index=1, close_candle_index=3, fill_ts=fill, close_ts=close,
        bars_held=2, value_per_point_per_lot=1.0, point=0.01, commission_usd=0.0,
    )


def _mtm(points: list[tuple[datetime, float]]):
    return points


class TestDaily:
    def test_trade_attributed_to_exit_date(self):
        # Fills Friday, closes Saturday (cross-midnight) → counts in Saturday
        trades = [_trade(FRIDAY, FRIDAY + timedelta(days=1, hours=2), 50.0)]
        rows = build_period_rows(trades, [], "daily")
        by_id = {r.period_id: r for r in rows}
        assert by_id["2026-11-06"].trade_count == 0
        assert by_id["2026-11-07"].trade_count == 1
        assert by_id["2026-11-07"].net_pnl == pytest.approx(50.0)
        # entry cohort reported separately
        assert by_id["2026-11-06"].entry_trade_count == 1
        assert by_id["2026-11-06"].entry_net_pnl == pytest.approx(50.0)

    def test_zero_trade_days_including_weekend_are_listed(self):
        mtm = [(FRIDAY, 10_000.0), (FRIDAY + timedelta(days=3), 10_100.0)]
        rows = build_period_rows([], mtm, "daily")
        ids = [r.period_id for r in rows]
        assert "2026-11-06" in ids
        assert "2026-11-07" in ids   # Saturday — no market, carried equity
        assert "2026-11-08" in ids   # Sunday
        assert "2026-11-09" in ids
        assert all(r.trade_count == 0 for r in rows)
        # Friday close carries through the weekend
        sat = next(r for r in rows if r.period_id == "2026-11-07")
        assert sat.equity_open == pytest.approx(10_000.0)
        assert sat.equity_close == pytest.approx(10_000.0)

    def test_daily_equity_and_drawdown(self):
        trades = [_trade(FRIDAY + timedelta(hours=1), FRIDAY + timedelta(hours=2), -200.0)]
        mtm = [
            (FRIDAY, 10_000.0),
            (FRIDAY + timedelta(hours=1, minutes=30), 9_700.0),  # floating loss
            (FRIDAY + timedelta(hours=2), 9_800.0),              # closed at -200
        ]
        rows = build_period_rows(trades, mtm, "daily")
        row = rows[0]
        assert row.period_id == "2026-11-06"
        assert row.equity_open == pytest.approx(10_000.0)
        assert row.equity_close == pytest.approx(9_800.0)
        assert row.equity_high == pytest.approx(10_000.0)
        assert row.equity_low == pytest.approx(9_700.0)
        assert row.return_pct == pytest.approx(-2.0)
        assert row.max_drawdown_usd == pytest.approx(300.0)

    def test_counts_reconcile(self):
        trades = [
            _trade(FRIDAY, FRIDAY + timedelta(minutes=30), 20.0),
            _trade(FRIDAY + timedelta(days=3), FRIDAY + timedelta(days=3, minutes=30), -10.0),
            _trade(FRIDAY, FRIDAY + timedelta(minutes=10), -5.0),
        ]
        rows = build_period_rows(trades, [], "daily")
        assert sum(r.trade_count for r in rows) == 3
        assert sum(r.entry_trade_count for r in rows) == 3
        assert sum(r.net_pnl for r in rows) == pytest.approx(5.0)


class TestWeekly:
    def test_iso_week_grouping(self):
        # 2026-11-06 is a Friday in ISO week 45; Monday 2026-11-09 is week 46.
        trades = [_trade(FRIDAY, FRIDAY + timedelta(hours=1), 30.0)]
        rows = build_period_rows(trades, [], "weekly")
        by_id = {r.period_id: r for r in rows}
        assert "2026-W45" in by_id
        assert by_id["2026-W45"].trade_count == 1

    def test_weekend_spans_two_iso_weeks(self):
        # Fill Friday, close Monday → exit-attributed to next ISO week.
        trades = [_trade(FRIDAY, FRIDAY + timedelta(days=3), -25.0)]
        rows = build_period_rows(trades, [], "weekly")
        by_id = {r.period_id: r for r in rows}
        assert by_id["2026-W45"].trade_count == 0
        assert by_id["2026-W45"].entry_trade_count == 1
        assert by_id["2026-W46"].trade_count == 1
        assert by_id["2026-W46"].net_pnl == pytest.approx(-25.0)

    def test_year_boundary_iso_week(self):
        # ISO rule: 2026-01-01 is a Thursday, so ISO week 2026-W01 begins on
        # Monday 2025-12-29 — both trades belong to the SAME ISO week 2026-W01
        # even though their calendar years differ.
        trades = [
            _trade(datetime(2025, 12, 29, 10, tzinfo=UTC),
                   datetime(2025, 12, 29, 11, tzinfo=UTC), 10.0),
            _trade(datetime(2026, 1, 1, 10, tzinfo=UTC),
                   datetime(2026, 1, 1, 11, tzinfo=UTC), -4.0),
        ]
        rows = build_period_rows(trades, [], "weekly")
        by_id = {r.period_id: r for r in rows}
        assert set(by_id) == {"2026-W01"}
        assert by_id["2026-W01"].trade_count == 2
        assert by_id["2026-W01"].net_pnl == pytest.approx(6.0)


class TestMonthly:
    def test_month_grouping_and_boundary(self):
        trades = [
            _trade(datetime(2026, 10, 31, 20, tzinfo=UTC),
                   datetime(2026, 10, 31, 21, tzinfo=UTC), 40.0),
            _trade(datetime(2026, 11, 2, 10, tzinfo=UTC),
                   datetime(2026, 11, 2, 11, tzinfo=UTC), -15.0),
        ]
        rows = build_period_rows(trades, [], "monthly")
        by_id = {r.period_id: r for r in rows}
        assert by_id["2026-10"].net_pnl == pytest.approx(40.0)
        assert by_id["2026-11"].net_pnl == pytest.approx(-15.0)

    def test_cross_month_trade_attributed_to_exit_month(self):
        trades = [_trade(datetime(2026, 10, 30, 10, tzinfo=UTC),
                         datetime(2026, 11, 2, 12, tzinfo=UTC), 60.0)]
        rows = build_period_rows(trades, [], "monthly")
        by_id = {r.period_id: r for r in rows}
        assert by_id["2026-10"].trade_count == 0
        assert by_id["2026-11"].trade_count == 1
        assert by_id["2026-10"].entry_trade_count == 1


class TestStreaksAndEmpty:
    def test_period_streaks_reported(self):
        mtm = [
            (datetime(2026, 11, 9, 0, tzinfo=UTC), 10_000.0),
            (datetime(2026, 11, 10, 0, tzinfo=UTC), 10_100.0),
            (datetime(2026, 11, 11, 0, tzinfo=UTC), 9_900.0),
        ]
        trades = [
            _trade(datetime(2026, 11, 9, 5, tzinfo=UTC),
                   datetime(2026, 11, 9, 6, tzinfo=UTC), 100.0),
            _trade(datetime(2026, 11, 10, 5, tzinfo=UTC),
                   datetime(2026, 11, 10, 6, tzinfo=UTC), 100.0),
            _trade(datetime(2026, 11, 11, 5, tzinfo=UTC),
                   datetime(2026, 11, 11, 6, tzinfo=UTC), -200.0),
        ]
        daily, weekly, monthly, streaks = analyze_periods(trades, mtm)
        assert streaks["daily"].max_profitable_periods == 2
        assert streaks["daily"].max_losing_periods == 1
        assert streaks["daily"].profitable_periods == 2
        assert streaks["daily"].losing_periods == 1

    def test_no_trades_no_data_returns_empty(self):
        daily, weekly, monthly, streaks = analyze_periods([], [])
        assert daily == [] and weekly == [] and monthly == []

    def test_no_trades_but_mtm_yields_zero_trade_periods(self):
        mtm = [
            (datetime(2026, 11, 9, 0, tzinfo=UTC), 10_000.0),
            (datetime(2026, 11, 11, 0, tzinfo=UTC), 10_000.0),
        ]
        daily, *_ = analyze_periods([], mtm)
        assert [r.period_id for r in daily] == ["2026-11-09", "2026-11-10", "2026-11-11"]
        assert all(r.net_pnl == 0.0 for r in daily)

    def test_unknown_granularity_rejected(self):
        with pytest.raises(ValueError, match="granularity"):
            build_period_rows([], [(FRIDAY, 10_000.0)], "hourly")


class TestReconciliationAcrossGranularities:
    def test_daily_weekly_monthly_net_pnl_match(self):
        trades = [
            _trade(FRIDAY, FRIDAY + timedelta(minutes=30), 20.0),
            _trade(FRIDAY, FRIDAY + timedelta(minutes=10), -5.0),
            _trade(FRIDAY + timedelta(days=3), FRIDAY + timedelta(days=3, minutes=30), -10.0),
            _trade(datetime(2026, 12, 1, 10, tzinfo=UTC),
                   datetime(2026, 12, 1, 11, tzinfo=UTC), 12.0),
        ]
        daily = build_period_rows(trades, [], "daily")
        weekly = build_period_rows(trades, [], "weekly")
        monthly = build_period_rows(trades, [], "monthly")
        expected = sum(t.net_pnl_usd for t in trades)
        assert sum(r.net_pnl for r in daily) == pytest.approx(expected)
        assert sum(r.net_pnl for r in weekly) == pytest.approx(expected)
        assert sum(r.net_pnl for r in monthly) == pytest.approx(expected)
        assert sum(r.trade_count for r in weekly) == 4
