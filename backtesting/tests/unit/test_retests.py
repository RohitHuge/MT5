"""Unit tests for retest + rejection detection (Phase 3 P3.11–P3.13)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mt5_liquidity.data.candles import Candle
from mt5_liquidity.strategy.market_structure import MSSSignal
from mt5_liquidity.strategy.retests import detect_retest

BASE = datetime(2025, 1, 6, 8, 0, tzinfo=timezone.utc)
M5 = timedelta(minutes=5)
POINT = 0.01
BAND_PTS = 5    # retest_band_points
TIMEOUT = 10    # retest_timeout_bars


def _ts(i: int) -> datetime:
    return BASE + i * M5


def _candle(i: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(timestamp=_ts(i), open=o, high=h, low=l, close=c, timeframe="M5")


def _bearish_mss(break_candle_index: int = 10, pivot_price: float = 995.0) -> MSSSignal:
    return MSSSignal(
        sweep_zone_id="BSL_000000",
        direction="BEARISH",
        broken_pivot_index=5,
        broken_pivot_price=pivot_price,
        broken_pivot_confirmed_at=7,
        break_candle_index=break_candle_index,
        break_candle_ts=_ts(break_candle_index),
        break_candle_close=pivot_price - 1.0,
        bars_after_sweep=3,
    )


def _bullish_mss(break_candle_index: int = 10, pivot_price: float = 1005.0) -> MSSSignal:
    return MSSSignal(
        sweep_zone_id="SSL_000000",
        direction="BULLISH",
        broken_pivot_index=5,
        broken_pivot_price=pivot_price,
        broken_pivot_confirmed_at=7,
        break_candle_index=break_candle_index,
        break_candle_ts=_ts(break_candle_index),
        break_candle_close=pivot_price + 1.0,
        bars_after_sweep=3,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_bearish_retest_detected():
    """
    Bearish MSS at bar 10 (level=995.0).
    Bar 12 trades up into band [994.95, 995.05] and closes below 995.0 → RetestSignal.
    """
    mss = _bearish_mss(break_candle_index=10, pivot_price=995.0)
    candles = [_candle(i, 994.0, 994.5, 993.5, 994.0) for i in range(25)]
    # Bar 12: high reaches 995.02 (into band), close at 994.60 (< 995.0 → rejection)
    candles[12] = _candle(12, 994.5, 995.02, 994.3, 994.60)

    result = detect_retest(
        m5_candles=candles,
        mss=mss,
        mss_candle_index=10,
        retest_band_points=BAND_PTS,
        retest_timeout_bars=TIMEOUT,
        point=POINT,
    )
    assert result is not None
    assert result.direction == "BEARISH"
    assert result.rejection_candle_index == 12
    assert result.structure_level == pytest.approx(995.0)
    assert result.signal_price == pytest.approx(994.60)
    assert result.bars_after_mss == 2  # 12 - 10 = 2


def test_bullish_retest_detected():
    """
    Bullish MSS at bar 10 (level=1005.0).
    Bar 13 trades down into band [1004.95, 1005.05] and closes above 1005.0 → RetestSignal.
    """
    mss = _bullish_mss(break_candle_index=10, pivot_price=1005.0)
    candles = [_candle(i, 1006.0, 1006.5, 1005.5, 1006.0) for i in range(25)]
    # Bar 13: low dips to 1004.98 (into band), close at 1005.40 (> 1005.0 → rejection)
    candles[13] = _candle(13, 1005.5, 1005.6, 1004.98, 1005.40)

    result = detect_retest(
        m5_candles=candles,
        mss=mss,
        mss_candle_index=10,
        retest_band_points=BAND_PTS,
        retest_timeout_bars=TIMEOUT,
        point=POINT,
    )
    assert result is not None
    assert result.direction == "BULLISH"
    assert result.rejection_candle_index == 13
    assert result.signal_price == pytest.approx(1005.40)


def test_retest_timeout_expires():
    """No retest within timeout → returns None."""
    mss = _bearish_mss(break_candle_index=10, pivot_price=995.0)
    # All candles stay well below the band — price never returns
    candles = [_candle(i, 990.0, 990.5, 989.5, 990.0) for i in range(30)]

    result = detect_retest(
        m5_candles=candles,
        mss=mss,
        mss_candle_index=10,
        retest_band_points=BAND_PTS,
        retest_timeout_bars=5,  # search bars 11-15 only
        point=POINT,
    )
    assert result is None


def test_no_rejection_no_signal():
    """
    Price enters the band but candle closes on the WRONG side → no RetestSignal.
    Bearish setup: candle trades into band but closes ABOVE structure level (bullish close).
    """
    mss = _bearish_mss(break_candle_index=10, pivot_price=995.0)
    candles = [_candle(i, 994.0, 994.5, 993.5, 994.0) for i in range(25)]
    # Bar 12: high reaches band, but close is ABOVE 995.0 → not a bearish rejection
    candles[12] = _candle(12, 994.5, 995.02, 994.3, 995.50)

    result = detect_retest(
        m5_candles=candles,
        mss=mss,
        mss_candle_index=10,
        retest_band_points=BAND_PTS,
        retest_timeout_bars=TIMEOUT,
        point=POINT,
    )
    assert result is None


def test_signal_price_equals_rejection_close():
    """signal_price must equal the rejection candle's close price."""
    mss = _bearish_mss(break_candle_index=10, pivot_price=995.0)
    candles = [_candle(i, 994.0, 994.5, 993.5, 994.0) for i in range(25)]
    close_price = 994.37
    candles[11] = _candle(11, 994.5, 995.01, 994.0, close_price)

    result = detect_retest(
        m5_candles=candles,
        mss=mss,
        mss_candle_index=10,
        retest_band_points=BAND_PTS,
        retest_timeout_bars=TIMEOUT,
        point=POINT,
    )
    assert result is not None
    assert result.signal_price == pytest.approx(close_price)
    assert result.rejection_candle_close == pytest.approx(close_price)
    assert not hasattr(result, "entry_price"), "entry_price must not exist; use signal_price"
