"""Unit tests for fill simulation (backtest/execution.py)."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta

from mt5_liquidity.backtest.execution import compute_fill_price, simulate_fill
from mt5_liquidity.data.candles import Candle

POINT = 0.01
BASE = datetime(2025, 1, 6, 0, 0, tzinfo=timezone.utc)


def _candle(i, o, h, l, c):
    return Candle(timestamp=BASE + timedelta(minutes=5 * i), open=o, high=h, low=l, close=c)


def _fill(direction, sl, signal_idx=0, spread=21, slip=3, rr=2.0, candles=None):
    if candles is None:
        candles = [_candle(i, 2000.0, 2010.0, 1990.0, 2005.0) for i in range(10)]
    return simulate_fill(
        setup_id="TEST",
        direction=direction,
        signal_candle_index=signal_idx,
        sl_price=sl,
        lots=0.01,
        m5_candles=candles,
        spread_points=spread,
        slippage_points=slip,
        rr_target=rr,
        point=POINT,
    )


def test_buy_fill_price():
    # BUY fill = open + (spread + slip) * point = 2000 + (21+3)*0.01 = 2000.24
    result = _fill("BUY", sl=1990.0)
    assert result is not None
    assert result.fill_price == pytest.approx(2000.0 + 24 * POINT)


def test_sell_fill_price():
    # SELL fill = open - slip*point = 2000 - 3*0.01 = 1999.97
    result = _fill("SELL", sl=2010.0)
    assert result is not None
    assert result.fill_price == pytest.approx(2000.0 - 3 * POINT)


def test_buy_tp_placement():
    result = _fill("BUY", sl=1990.0, rr=2.0)
    sl_dist = abs(result.fill_price - result.sl_price)
    assert result.tp_price == pytest.approx(result.fill_price + sl_dist * 2.0)


def test_sell_tp_placement():
    result = _fill("SELL", sl=2010.0, rr=2.0)
    sl_dist = abs(result.fill_price - result.sl_price)
    assert result.tp_price == pytest.approx(result.fill_price - sl_dist * 2.0)


def test_fill_uses_next_candle():
    candles = [_candle(0, 2000.0, 2010.0, 1990.0, 2005.0),
               _candle(1, 2050.0, 2060.0, 2040.0, 2055.0)]
    result = _fill("BUY", sl=1990.0, signal_idx=0, candles=candles)
    # Fill should use candle index 1 (open=2050)
    assert result.fill_candle_index == 1
    assert result.fill_price == pytest.approx(2050.0 + 24 * POINT)


def test_returns_none_when_no_next_bar():
    candles = [_candle(0, 2000.0, 2010.0, 1990.0, 2005.0)]
    result = _fill("BUY", sl=1990.0, signal_idx=0, candles=candles)
    assert result is None


# --- compute_fill_price (Fix 1 helper) ---

def test_compute_fill_price_buy():
    price = compute_fill_price("BUY", candle_open=2000.0, spread_points=21, slippage_points=3, point=POINT)
    assert price == pytest.approx(2000.0 + 24 * POINT)


def test_compute_fill_price_sell():
    price = compute_fill_price("SELL", candle_open=2000.0, spread_points=21, slippage_points=3, point=POINT)
    assert price == pytest.approx(2000.0 - 3 * POINT)


def test_compute_fill_price_zero_spread_zero_slip():
    buy  = compute_fill_price("BUY",  candle_open=2000.0, spread_points=0, slippage_points=0, point=POINT)
    sell = compute_fill_price("SELL", candle_open=2000.0, spread_points=0, slippage_points=0, point=POINT)
    assert buy  == pytest.approx(2000.0)
    assert sell == pytest.approx(2000.0)


def test_simulate_fill_uses_compute_fill_price_internally():
    # simulate_fill must produce the same fill_price as compute_fill_price
    candles = [
        _candle(0, 2000.0, 2010.0, 1990.0, 2005.0),
        _candle(1, 2050.0, 2060.0, 2040.0, 2055.0),
    ]
    result = _fill("BUY", sl=1990.0, signal_idx=0, spread=21, slip=3, candles=candles)
    expected = compute_fill_price("BUY", 2050.0, 21, 3, POINT)
    assert result.fill_price == pytest.approx(expected)
