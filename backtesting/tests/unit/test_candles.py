"""Unit tests for candle aggregation."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from mt5_liquidity.data.candles import AggregationResult, Candle, aggregate_m5_to_m15
from tests.fixtures.candle_fixtures import make_candle, BASE_TIME, M5


def _m5_at(minute_offset: int, o: float, h: float, l: float, c: float) -> Candle:
    ts = BASE_TIME + timedelta(minutes=minute_offset)
    return make_candle(ts, o, h, l, c)


# ── Original tests (updated for AggregationResult return type) ───────────────

def test_three_m5_to_one_m15():
    candles = [
        _m5_at(0, 100, 105, 99, 103),
        _m5_at(5, 103, 107, 102, 106),
        _m5_at(10, 106, 108, 104, 107),
    ]
    res = aggregate_m5_to_m15(candles)
    assert isinstance(res, AggregationResult)
    assert len(res.candles) == 1
    assert res.incomplete_windows == []
    m15 = res.candles[0]
    assert m15.open == 100
    assert m15.high == 108
    assert m15.low == 99
    assert m15.close == 107
    assert m15.timestamp == candles[0].timestamp.replace(minute=0, second=0, microsecond=0)
    assert m15.timeframe == "M15"


def test_six_m5_to_two_m15():
    candles = [
        _m5_at(0,  100, 105, 99,  103),
        _m5_at(5,  103, 107, 102, 106),
        _m5_at(10, 106, 108, 104, 107),
        _m5_at(15, 200, 205, 199, 203),
        _m5_at(20, 203, 207, 202, 206),
        _m5_at(25, 206, 208, 204, 207),
    ]
    res = aggregate_m5_to_m15(candles)
    assert len(res.candles) == 2
    assert res.incomplete_windows == []
    assert res.candles[0].open == 100
    assert res.candles[1].open == 200


def test_incomplete_group_reported():
    """4 M5 candles: first window of 3 emits 1 M15, leftover 1 is reported as incomplete."""
    candles = [
        _m5_at(0,  100, 105, 99,  103),
        _m5_at(5,  103, 107, 102, 106),
        _m5_at(10, 106, 108, 104, 107),
        _m5_at(15, 200, 205, 199, 203),  # starts new M15 window, only 1 candle
    ]
    res = aggregate_m5_to_m15(candles)
    assert len(res.candles) == 1
    assert len(res.incomplete_windows) == 1
    w = res.incomplete_windows[0]
    assert w["expected"] == 3
    assert w["got"] == 1
    assert w["window_start"].minute % 15 == 0


def test_empty_input():
    res = aggregate_m5_to_m15([])
    assert isinstance(res, AggregationResult)
    assert res.candles == []
    assert res.incomplete_windows == []


def test_m15_timestamp_is_15min_boundary():
    candles = [
        _m5_at(0,  100, 105, 99,  103),
        _m5_at(5,  103, 107, 102, 106),
        _m5_at(10, 106, 108, 104, 107),
    ]
    res = aggregate_m5_to_m15(candles)
    ts = res.candles[0].timestamp
    assert ts.minute % 15 == 0
    assert ts.second == 0


def test_m15_volume_sum_spread_max():
    candles = [
        Candle(BASE_TIME + timedelta(minutes=0),  100, 105, 99,  103, volume=10, spread=2),
        Candle(BASE_TIME + timedelta(minutes=5),  103, 107, 102, 106, volume=20, spread=3),
        Candle(BASE_TIME + timedelta(minutes=10), 106, 108, 104, 107, volume=15, spread=1),
    ]
    res = aggregate_m5_to_m15(candles)
    assert res.candles[0].volume == 45
    assert res.candles[0].spread == 3


# ── New acceptance tests ──────────────────────────────────────────────────────

def test_window_with_two_m5_reported_as_incomplete():
    """A window with only 2 M5 candles must appear in incomplete_windows, not be dropped silently."""
    candles = [
        _m5_at(0, 100, 105, 99, 103),
        _m5_at(5, 103, 107, 102, 106),
        # minute=10 is missing — only 2 of 3 candles in this window
    ]
    res = aggregate_m5_to_m15(candles)
    assert res.candles == []
    assert len(res.incomplete_windows) == 1
    w = res.incomplete_windows[0]
    assert w["got"] == 2
    assert w["expected"] == 3
    assert len(w["candles"]) == 2


def test_out_of_order_m5_in_window_raises():
    """Out-of-order M5 candles within the same window must raise ValueError."""
    candles = [
        _m5_at(10, 103, 107, 102, 106),
        _m5_at(5,  100, 105, 99,  103),   # earlier timestamp second
        _m5_at(0,  106, 108, 104, 107),   # earliest timestamp last
    ]
    with pytest.raises(ValueError, match="Out-of-order"):
        aggregate_m5_to_m15(candles)


def test_candles_spanning_boundary_split_correctly():
    """
    2 M5 candles in window A (00:00–00:14) and 3 in window B (00:15–00:29).
    Window A must be incomplete; window B must produce 1 M15 candle.
    """
    candles = [
        _m5_at(0,  100, 105, 99,  103),   # window 00:00
        _m5_at(5,  103, 107, 102, 106),   # window 00:00
        # minute 10 missing — window 00:00 incomplete
        _m5_at(15, 200, 205, 199, 203),   # window 00:15
        _m5_at(20, 203, 207, 202, 206),   # window 00:15
        _m5_at(25, 206, 208, 204, 207),   # window 00:15
    ]
    res = aggregate_m5_to_m15(candles)
    assert len(res.candles) == 1
    assert res.candles[0].open == 200
    assert len(res.incomplete_windows) == 1
    assert res.incomplete_windows[0]["got"] == 2


def test_naive_timestamp_raises():
    """Candle with naive (non-UTC) timestamp must raise ValueError."""
    with pytest.raises(ValueError, match="UTC-aware"):
        Candle(datetime(2025, 1, 6, 0, 0), 100, 105, 99, 103)
