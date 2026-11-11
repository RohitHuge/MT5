"""Unit tests for shared analytics calculations (reporting/analytics/aggregation.py)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mt5_liquidity.backtest.positions import ClosedPosition
from mt5_liquidity.reporting.analytics.aggregation import (
    aggregate_trade_stats,
    max_drawdown_from_series,
    median,
    period_streaks,
    trade_duration_minutes,
)

UTC = timezone.utc
BASE = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def _trade(
    pnl: float,
    commission: float = 0.0,
    fill_ts: datetime | None = None,
    close_ts: datetime | None = None,
    setup_id: str = "T",
) -> ClosedPosition:
    return ClosedPosition(
        setup_id=setup_id, direction="BUY", fill_price=2000.0,
        close_price=2001.0, tp_price=2004.0, sl_price=1998.0,
        lots=0.1, pnl_usd=pnl, exit_reason="TP" if pnl > 0 else "SL",
        fill_candle_index=1, close_candle_index=3,
        fill_ts=fill_ts or BASE, close_ts=close_ts or BASE,
        bars_held=2, value_per_point_per_lot=1.0, point=0.01,
        commission_usd=commission,
    )


class TestAggregateTradeStats:
    def test_empty_group_is_zeroed(self):
        stats = aggregate_trade_stats([])
        assert stats.trade_count == 0
        assert stats.win_rate == 0.0
        assert stats.net_pnl == 0.0
        assert stats.profit_factor == 0.0

    def test_win_loss_and_breakeven_classification(self):
        trades = [_trade(50.0), _trade(-30.0), _trade(0.0)]
        stats = aggregate_trade_stats(trades)
        assert stats.trade_count == 3
        assert stats.winners == 1
        assert stats.losers == 2          # ledger convention: net <= 0 is a loser
        assert stats.breakeven == 1       # net == 0 subset reported separately
        assert stats.winners + stats.losers == stats.trade_count

    def test_net_pnl_subtracts_commission(self):
        stats = aggregate_trade_stats([_trade(50.0, commission=5.0)])
        assert stats.net_pnl == pytest.approx(45.0)
        assert stats.total_commission == pytest.approx(5.0)

    def test_gross_profit_and_loss_match_ledger_semantics(self):
        trades = [_trade(20.0, commission=1.0), _trade(-5.0, commission=1.0)]
        stats = aggregate_trade_stats(trades)
        assert stats.gross_profit == pytest.approx(20.0)   # gross pnl > 0
        assert stats.gross_loss == pytest.approx(-5.0)
        assert stats.profit_factor == pytest.approx(4.0)

    def test_profit_factor_infinite_when_no_losses(self):
        stats = aggregate_trade_stats([_trade(10.0)])
        assert stats.profit_factor == float("inf")

    def test_averages(self):
        trades = [_trade(30.0), _trade(-10.0, commission=2.0)]
        stats = aggregate_trade_stats(trades)
        assert stats.avg_win == pytest.approx(30.0)
        assert stats.avg_loss == pytest.approx(-12.0)      # net of commission
        assert stats.avg_net_pnl == pytest.approx((30.0 - 12.0) / 2)

    def test_ordering_does_not_change_outputs(self):
        trades = [_trade(20.0), _trade(-5.0), _trade(30.0), _trade(-8.0)]
        forward = aggregate_trade_stats(trades)
        backward = aggregate_trade_stats(list(reversed(trades)))
        assert forward == backward


class TestMaxDrawdown:
    def test_empty_series(self):
        dd = max_drawdown_from_series([])
        assert dd.max_drawdown_usd == 0.0
        assert dd.peak_equity is None

    def test_drawdown_with_peak_trough_recovery(self):
        t0 = BASE
        series = [
            (t0, 10_000.0),
            (t0 + timedelta(minutes=5), 11_000.0),   # peak
            (t0 + timedelta(minutes=10), 10_500.0),
            (t0 + timedelta(minutes=15), 10_200.0),  # trough
            (t0 + timedelta(minutes=20), 11_000.0),  # recovery
        ]
        dd = max_drawdown_from_series(series)
        assert dd.peak_equity == pytest.approx(11_000.0)
        assert dd.peak_ts == t0 + timedelta(minutes=5)
        assert dd.trough_equity == pytest.approx(10_200.0)
        assert dd.trough_ts == t0 + timedelta(minutes=15)
        assert dd.max_drawdown_usd == pytest.approx(800.0)
        assert dd.max_drawdown_pct == pytest.approx(800.0 / 11_000.0 * 100.0)
        assert dd.recovery_ts == t0 + timedelta(minutes=20)
        assert dd.underwater_minutes == pytest.approx(15.0)

    def test_unrecovered_drawdown_runs_to_series_end(self):
        series = [
            (BASE, 10_000.0),
            (BASE + timedelta(minutes=5), 11_000.0),
            (BASE + timedelta(minutes=15), 10_000.0),
        ]
        dd = max_drawdown_from_series(series)
        assert dd.max_drawdown_usd == pytest.approx(1_000.0)
        assert dd.recovery_ts is None
        assert dd.underwater_minutes == pytest.approx(10.0)

    def test_monotonic_upward_series_has_no_drawdown(self):
        series = [(BASE, 10_000.0), (BASE + timedelta(minutes=5), 10_100.0)]
        dd = max_drawdown_from_series(series)
        assert dd.max_drawdown_usd == 0.0

    def test_new_higher_peak_resets_tracking(self):
        series = [
            (BASE, 10_000.0),
            (BASE + timedelta(minutes=5), 12_000.0),
            (BASE + timedelta(minutes=10), 11_000.0),   # dd 1000 vs peak 12000
            (BASE + timedelta(minutes=15), 13_000.0),   # new peak
            (BASE + timedelta(minutes=20), 12_500.0),   # dd 500 vs new peak
        ]
        dd = max_drawdown_from_series(series)
        # The summary describes the MAXIMUM drawdown event: its peak is the
        # 12_000 high (not the later 13_000 peak, whose dd is only 500).
        assert dd.peak_equity == pytest.approx(12_000.0)
        assert dd.peak_ts == BASE + timedelta(minutes=5)
        assert dd.trough_equity == pytest.approx(11_000.0)
        assert dd.trough_ts == BASE + timedelta(minutes=10)
        assert dd.max_drawdown_usd == pytest.approx(1_000.0)
        assert dd.recovery_ts == BASE + timedelta(minutes=15)


class TestPeriodStreaks:
    def test_streaks_and_counts(self):
        result = period_streaks([100.0, 100.0, -50.0, -50.0, -50.0, 0.0, 25.0])
        max_profit, max_loss, n_profit, n_loss, n_flat = result
        assert max_profit == 2
        assert max_loss == 3
        assert n_profit == 3          # two wins then one later win
        assert n_loss == 3            # each losing period counts individually
        assert n_flat == 1

    def test_flat_breaks_both_streaks(self):
        max_profit, max_loss, *_ = period_streaks([10.0, 0.0, 10.0])
        assert max_profit == 1
        max_profit, max_loss, *_ = period_streaks([-10.0, 0.0, -10.0])
        assert max_loss == 1

    def test_empty(self):
        assert period_streaks([]) == (0, 0, 0, 0, 0)


class TestTradeDuration:
    def test_duration_minutes(self):
        trade = _trade(10.0, fill_ts=BASE, close_ts=BASE + timedelta(minutes=65))
        assert trade_duration_minutes(trade) == pytest.approx(65.0)

    def test_zero_duration_same_candle(self):
        trade = _trade(10.0, fill_ts=BASE, close_ts=BASE)
        assert trade_duration_minutes(trade) == 0.0

    def test_missing_timestamps_raise(self):
        trade = _trade(10.0, fill_ts=None, close_ts=None)
        trade.fill_ts = None  # type: ignore[assignment]
        with pytest.raises(ValueError, match="non-datetime"):
            trade_duration_minutes(trade)

    def test_inverted_timestamps_raise(self):
        trade = _trade(10.0, fill_ts=BASE + timedelta(minutes=10), close_ts=BASE)
        with pytest.raises(ValueError, match="before it fills"):
            trade_duration_minutes(trade)


class TestMedian:
    def test_odd_and_even(self):
        assert median([3.0, 1.0, 2.0]) == 2.0
        assert median([4.0, 1.0, 2.0, 3.0]) == 2.5

    def test_empty_returns_none(self):
        assert median([]) is None
