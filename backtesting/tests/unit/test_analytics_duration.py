"""Unit tests for duration analytics (reporting/analytics/duration.py)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mt5_liquidity.backtest.positions import ClosedPosition
from mt5_liquidity.reporting.analytics.duration import (
    DEFAULT_BUCKET_MINUTES,
    analyze_duration,
    bucket_labels,
)

UTC = timezone.utc
BASE = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def _trade(duration_minutes: float | None, pnl: float = 10.0) -> ClosedPosition:
    close_ts = BASE if duration_minutes is None else BASE + timedelta(minutes=duration_minutes)
    return ClosedPosition(
        setup_id=f"T{duration_minutes}", direction="BUY", fill_price=2000.0,
        close_price=2001.0, tp_price=2004.0, sl_price=1998.0,
        lots=0.1, pnl_usd=pnl, exit_reason="TP" if pnl > 0 else "SL",
        fill_candle_index=1, close_candle_index=3, fill_ts=BASE, close_ts=close_ts,
        bars_held=2, value_per_point_per_lot=1.0, point=0.01, commission_usd=0.0,
    )


def test_bucket_labels_defaults():
    labels = bucket_labels(DEFAULT_BUCKET_MINUTES)
    assert labels == ["0-5m", "5-15m", "15-30m", "30-60m", "60-120m", "120m+"]


def test_trades_land_in_expected_buckets():
    trades = [
        _trade(3.0),      # 0-5m
        _trade(10.0),     # 5-15m
        _trade(20.0),     # 15-30m
        _trade(45.0),     # 30-60m
        _trade(90.0),     # 60-120m
        _trade(300.0),    # 120m+
    ]
    result = analyze_duration(trades)
    counts = [b.stats.trade_count for b in result.buckets]
    assert counts == [1, 1, 1, 1, 1, 1]


def test_exact_boundaries_go_to_higher_bucket():
    # Lower-inclusive / upper-exclusive: exactly 5 -> 5-15m, exactly 120 -> 120m+
    trades = [_trade(5.0), _trade(15.0), _trade(120.0)]
    result = analyze_duration(trades)
    counts = [b.stats.trade_count for b in result.buckets]
    assert counts == [0, 1, 1, 0, 0, 1]


def test_zero_duration_and_same_candle_count():
    result = analyze_duration([_trade(None), _trade(0.0), _trade(7.0)])
    assert result.buckets[0].stats.trade_count == 2   # 0-5m holds both zero-duration
    assert result.same_candle_closes == 2
    assert result.min_duration_minutes == 0.0


def test_duration_statistics():
    trades = [_trade(10.0), _trade(30.0), _trade(60.0)]
    result = analyze_duration(trades)
    assert result.avg_duration_minutes == pytest.approx(100.0 / 3)
    assert result.median_duration_minutes == pytest.approx(30.0)
    assert result.min_duration_minutes == pytest.approx(10.0)
    assert result.max_duration_minutes == pytest.approx(60.0)


def test_winners_and_losers_split():
    trades = [_trade(10.0, pnl=50.0), _trade(20.0, pnl=-30.0), _trade(40.0, pnl=-20.0)]
    result = analyze_duration(trades)
    assert result.winners.trade_count == 1
    assert result.losers.trade_count == 2
    assert result.winners.net_pnl == pytest.approx(50.0)
    assert result.losers.net_pnl == pytest.approx(-50.0)


def test_bucket_stats_reconcile_to_population():
    trades = [_trade(m, pnl=float(i) - 3) for i, m in enumerate([2, 8, 25, 70, 200, 0])]
    result = analyze_duration(trades)
    total = sum(b.stats.trade_count for b in result.buckets)
    net = sum(b.stats.net_pnl for b in result.buckets)
    assert total == len(trades)
    assert net == pytest.approx(sum(t.net_pnl_usd for t in trades))


def test_empty_trades():
    result = analyze_duration([])
    assert all(b.stats.trade_count == 0 for b in result.buckets)
    assert result.avg_duration_minutes is None
    assert result.median_duration_minutes is None
    assert result.same_candle_closes == 0


def test_custom_bucket_edges():
    result = analyze_duration([_trade(3.0), _trade(10.0), _trade(40.0)],
                              bucket_edges=(10, 30))
    labels = [b.label for b in result.buckets]
    assert labels == ["0-10m", "10-30m", "30m+"]
    counts = [b.stats.trade_count for b in result.buckets]
    assert counts == [1, 1, 1]


def test_invalid_bucket_edges_rejected():
    with pytest.raises(ValueError, match="strictly increasing"):
        analyze_duration([_trade(1.0)], bucket_edges=(10, 10))
    with pytest.raises(ValueError, match="positive"):
        analyze_duration([_trade(1.0)], bucket_edges=(0, 10))
    with pytest.raises(ValueError, match="strictly increasing"):
        analyze_duration([_trade(1.0)], bucket_edges=(30, 10))
