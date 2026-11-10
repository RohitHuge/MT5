"""Unit tests for candle series validation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mt5_liquidity.data.candles import Candle
from mt5_liquidity.data.validation import validate_candles
from tests.fixtures.candle_fixtures import BASE_TIME, M5, make_candle


def _series(n: int, price: float = 1000.0) -> list[Candle]:
    return [
        make_candle(BASE_TIME + i * M5, price, price + 1, price - 1, price)
        for i in range(n)
    ]


def test_valid_series_passes():
    candles = _series(10)
    result = validate_candles(candles, "M5")
    assert result.passed is True
    assert result.candle_count == 10
    assert result.duplicate_timestamps == []
    assert result.ohlc_violations == []
    assert result.out_of_order == []


def test_out_of_order_detected():
    candles = _series(5)
    # Swap candles[1] and candles[2]
    candles[1], candles[2] = candles[2], candles[1]
    result = validate_candles(candles, "M5")
    assert result.passed is False
    assert len(result.out_of_order) > 0


def test_duplicate_timestamp_detected():
    candles = _series(5)
    candles[3] = make_candle(candles[2].timestamp, 1000, 1001, 999, 1000)
    result = validate_candles(candles, "M5")
    assert result.passed is False
    assert len(result.duplicate_timestamps) > 0


def test_ohlc_high_less_than_open():
    candles = _series(3)
    # H < O violation
    candles[1] = make_candle(candles[1].timestamp, 1000, 999, 998, 1000)
    result = validate_candles(candles, "M5")
    assert result.passed is False
    assert any("High" in msg for _, msg in result.ohlc_violations)


def test_ohlc_low_greater_than_close():
    candles = _series(3)
    # L > C violation
    candles[1] = make_candle(candles[1].timestamp, 1000, 1002, 1005, 1001)
    result = validate_candles(candles, "M5")
    assert result.passed is False
    assert any("Low" in msg for _, msg in result.ohlc_violations)


def test_ohlc_high_less_than_low():
    candles = _series(3)
    # H < L
    candles[1] = Candle(
        timestamp=candles[1].timestamp,
        open=1000, high=995, low=998, close=1000,
    )
    result = validate_candles(candles, "M5")
    assert result.passed is False
    assert any("High" in msg and "Low" in msg for _, msg in result.ohlc_violations)


def test_weekday_gap_detected():
    """Gap of 30 minutes mid-week should be detected."""
    candles = _series(5)
    # Insert a 30-minute gap between candle 2 and 3
    gap_ts = candles[2].timestamp + timedelta(minutes=25)
    candles[3] = make_candle(gap_ts, 1000, 1001, 999, 1000)
    candles[4] = make_candle(gap_ts + M5, 1000, 1001, 999, 1000)
    result = validate_candles(candles[:5], "M5")
    assert len(result.gaps) > 0


def test_weekend_gap_not_flagged():
    """
    Gap from Friday ~22:00 UTC to Sunday ~22:00 UTC should NOT be reported as a gap.
    """
    friday_close = datetime(2025, 1, 10, 22, 0, tzinfo=timezone.utc)
    sunday_open  = datetime(2025, 1, 12, 22, 0, tzinfo=timezone.utc)
    candles = [
        make_candle(friday_close, 1000, 1001, 999, 1000),
        make_candle(sunday_open,  1000, 1001, 999, 1000),
    ]
    result = validate_candles(candles, "M5")
    assert len(result.gaps) == 0


def test_empty_series_no_crash():
    result = validate_candles([], "M5")
    assert result.candle_count == 0
    assert result.passed is True
