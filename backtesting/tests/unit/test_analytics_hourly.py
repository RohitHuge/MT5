"""Unit tests for hourly analytics (reporting/analytics/hourly.py)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mt5_liquidity.backtest.positions import ClosedPosition
from mt5_liquidity.reporting.analytics.hourly import analyze_hours

UTC = timezone.utc
BASE = datetime(2026, 11, 10, 0, 0, tzinfo=UTC)


def _trade_at(hour: int, minute: int = 0, pnl: float = 10.0) -> ClosedPosition:
    fill_ts = BASE + timedelta(hours=hour, minutes=minute)
    return ClosedPosition(
        setup_id=f"H{hour}{minute}", direction="BUY", fill_price=2000.0,
        close_price=2001.0, tp_price=2004.0, sl_price=1998.0,
        lots=0.1, pnl_usd=pnl, exit_reason="TP" if pnl > 0 else "SL",
        fill_candle_index=1, close_candle_index=3, fill_ts=fill_ts,
        close_ts=fill_ts + timedelta(minutes=5), bars_held=1,
        value_per_point_per_lot=1.0, point=0.01, commission_usd=0.0,
    )


def test_returns_exactly_24_rows():
    rows = analyze_hours([])
    assert len(rows) == 24
    assert [r.hour_utc for r in rows] == list(range(24))


def test_trades_grouped_by_utc_entry_hour():
    trades = [_trade_at(9, pnl=30.0), _trade_at(9, 30, pnl=-10.0), _trade_at(15)]
    rows = analyze_hours(trades)
    hour9 = rows[9]
    assert hour9.stats.trade_count == 2
    assert hour9.stats.net_pnl == pytest.approx(20.0)
    assert rows[15].stats.trade_count == 1
    assert rows[8].stats.trade_count == 0


def test_empty_hours_have_zeroed_stats():
    rows = analyze_hours([_trade_at(5)])
    for hour in (4, 6):
        assert rows[hour].stats.trade_count == 0
        assert rows[hour].stats.net_pnl == 0.0
        assert rows[hour].drawdown_contribution == 0.0


def test_midnight_boundary_hour_23_and_0():
    trades = [_trade_at(23), _trade_at(0)]
    rows = analyze_hours(trades)
    assert rows[23].stats.trade_count == 1
    assert rows[0].stats.trade_count == 1


def test_counts_reconcile_to_population():
    trades = [_trade_at(h % 24, pnl=float(h)) for h in range(30)]
    rows = analyze_hours(trades)
    assert sum(r.stats.trade_count for r in rows) == len(trades)
    assert sum(r.stats.net_pnl for r in rows) == pytest.approx(
        sum(t.net_pnl_usd for t in trades))


def test_drawdown_contribution_sums_losing_trades_only():
    trades = [_trade_at(3, pnl=40.0), _trade_at(3, pnl=-15.0), _trade_at(3, pnl=-5.0)]
    rows = analyze_hours(trades)
    assert rows[3].drawdown_contribution == pytest.approx(-20.0)


def test_display_timezone_conversion():
    # UTC+5:30 (Asia/Kolkata): UTC hour 20 -> local 01:30 next day -> hour 1
    rows = analyze_hours([_trade_at(20)], display_timezone="Asia/Kolkata")
    assert rows[20].hour_display == 1
    # UTC hour 0 -> 05:30 local -> hour 5
    rows = analyze_hours([_trade_at(0)], display_timezone="Asia/Kolkata")
    assert rows[0].hour_display == 5


def test_display_timezone_utc_identity():
    rows = analyze_hours([_trade_at(17)], display_timezone="UTC")
    assert rows[17].hour_display == 17


def test_unknown_display_timezone_raises():
    with pytest.raises(ValueError, match="Unknown analytics_display_timezone"):
        analyze_hours([], display_timezone="Not/AZone")
