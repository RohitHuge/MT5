"""Unit tests for candle aggregation."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from mt5_liquidity.data.candles import Candle, aggregate_m5_to_m15
from tests.fixtures.candle_fixtures import make_candle, BASE_TIME, M5


def _m5_at(minute_offset: int, o: float, h: float, l: float, c: float) -> Candle:
    ts = BASE_TIME + timedelta(minutes=minute_offset)
    return make_candle(ts, o, h, l, c)


def test_three_m5_to_one_m15():
    candles = [
        _m5_at(0, 100, 105, 99, 103),
        _m5_at(5, 103, 107, 102, 106),
        _m5_at(10, 106, 108, 104, 107),
    ]
    result = aggregate_m5_to_m15(candles)
    assert len(result) == 1
    m15 = result[0]
    assert m15.open == 100
    assert m15.high == 108
    assert m15.low == 99
    assert m15.close == 107
    assert m15.timestamp == candles[0].timestamp
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
    result = aggregate_m5_to_m15(candles)
    assert len(result) == 2
    assert result[0].open == 100
    assert result[1].open == 200


def test_incomplete_group_dropped():
    """4 M5 candles: first group of 3 emits 1 M15, leftover 1 is dropped."""
    candles = [
        _m5_at(0,  100, 105, 99,  103),
        _m5_at(5,  103, 107, 102, 106),
        _m5_at(10, 106, 108, 104, 107),
        _m5_at(15, 200, 205, 199, 203),
    ]
    result = aggregate_m5_to_m15(candles)
    assert len(result) == 1


def test_empty_input():
    assert aggregate_m5_to_m15([]) == []


def test_m15_timestamp_is_15min_boundary():
    candles = [
        _m5_at(0,  100, 105, 99,  103),
        _m5_at(5,  103, 107, 102, 106),
        _m5_at(10, 106, 108, 104, 107),
    ]
    result = aggregate_m5_to_m15(candles)
    ts = result[0].timestamp
    assert ts.minute % 15 == 0
    assert ts.second == 0


def test_m15_volume_sum_spread_max():
    candles = [
        Candle(BASE_TIME + timedelta(minutes=0),  100, 105, 99,  103, volume=10, spread=2),
        Candle(BASE_TIME + timedelta(minutes=5),  103, 107, 102, 106, volume=20, spread=3),
        Candle(BASE_TIME + timedelta(minutes=10), 106, 108, 104, 107, volume=15, spread=1),
    ]
    result = aggregate_m5_to_m15(candles)
    assert result[0].volume == 45
    assert result[0].spread == 3
