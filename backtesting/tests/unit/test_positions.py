"""Unit tests for open position tracking (backtest/positions.py)."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta

from mt5_liquidity.backtest.execution import FillResult
from mt5_liquidity.backtest.positions import OpenPosition, PositionStatus

POINT = 0.01
VPP = 1.0  # value_per_point_per_lot for XAUUSD
BASE = datetime(2025, 1, 6, 0, 0, tzinfo=timezone.utc)


def _fill(direction, fill_price, sl, tp, lots=0.01):
    return FillResult(
        setup_id="T",
        direction=direction,
        fill_price=fill_price,
        sl_price=sl,
        tp_price=tp,
        lots=lots,
        fill_candle_index=0,
        fill_candle_ts=BASE,
    )


def _pos(direction, fill_price, sl, tp, lots=0.01):
    return OpenPosition(
        fill=_fill(direction, fill_price, sl, tp, lots),
        value_per_point_per_lot=VPP,
        point=POINT,
    )


def _candle(o, h, l, c):
    from mt5_liquidity.data.candles import Candle
    return Candle(timestamp=BASE + timedelta(minutes=5), open=o, high=h, low=l, close=c)


# --- BUY position tests ---

def test_buy_tp_hit():
    pos = _pos("BUY", fill_price=2000.0, sl=1990.0, tp=2020.0)
    candle = _candle(2001.0, 2025.0, 2000.0, 2022.0)  # high touches TP
    status, close_price = pos.check_candle(candle, 1)
    assert status == PositionStatus.HIT_TP
    assert close_price == pytest.approx(2020.0)


def test_buy_sl_hit():
    pos = _pos("BUY", fill_price=2000.0, sl=1990.0, tp=2020.0)
    candle = _candle(1999.0, 1999.0, 1985.0, 1986.0)  # low below SL
    status, close_price = pos.check_candle(candle, 1)
    assert status == PositionStatus.HIT_SL
    assert close_price == pytest.approx(1990.0)


def test_buy_ambiguous_bar_sl_wins():
    # Both SL and TP in same candle — SL first (conservative)
    pos = _pos("BUY", fill_price=2000.0, sl=1990.0, tp=2020.0)
    candle = _candle(1995.0, 2025.0, 1985.0, 2000.0)
    status, close_price = pos.check_candle(candle, 1)
    assert status == PositionStatus.HIT_SL
    assert close_price == pytest.approx(1990.0)


def test_buy_gap_through_sl():
    pos = _pos("BUY", fill_price=2000.0, sl=1990.0, tp=2020.0)
    candle = _candle(1980.0, 1985.0, 1975.0, 1983.0)  # opens below SL
    status, close_price = pos.check_candle(candle, 1)
    assert status == PositionStatus.HIT_SL
    assert close_price == pytest.approx(1980.0)  # fills at open (gap)


def test_buy_still_open():
    pos = _pos("BUY", fill_price=2000.0, sl=1990.0, tp=2020.0)
    candle = _candle(2001.0, 2010.0, 1998.0, 2005.0)
    status, _ = pos.check_candle(candle, 1)
    assert status == PositionStatus.OPEN


def test_buy_pnl_on_tp():
    lots = 0.10
    pos = _pos("BUY", fill_price=2000.0, sl=1980.0, tp=2040.0, lots=lots)
    closed = pos.close(2040.0, 5, BASE + timedelta(minutes=25), "TP")
    # price_diff = 40, sl_points = (40/0.01) = 4000, pnl = (40/0.01)*1.0*0.10 = 400
    assert closed.pnl_usd == pytest.approx(400.0)


# --- SELL position tests ---

def test_sell_tp_hit():
    pos = _pos("SELL", fill_price=2000.0, sl=2010.0, tp=1980.0)
    candle = _candle(1999.0, 2000.0, 1975.0, 1990.0)  # low touches TP
    status, close_price = pos.check_candle(candle, 1)
    assert status == PositionStatus.HIT_TP
    assert close_price == pytest.approx(1980.0)


def test_sell_sl_hit():
    pos = _pos("SELL", fill_price=2000.0, sl=2010.0, tp=1980.0)
    candle = _candle(2005.0, 2015.0, 2004.0, 2012.0)  # high above SL
    status, close_price = pos.check_candle(candle, 1)
    assert status == PositionStatus.HIT_SL
    assert close_price == pytest.approx(2010.0)


def test_sell_gap_through_sl():
    pos = _pos("SELL", fill_price=2000.0, sl=2010.0, tp=1980.0)
    candle = _candle(2020.0, 2025.0, 2015.0, 2018.0)  # opens above SL
    status, close_price = pos.check_candle(candle, 1)
    assert status == PositionStatus.HIT_SL
    assert close_price == pytest.approx(2020.0)


def test_sell_pnl_on_sl_loss():
    lots = 0.01
    pos = _pos("SELL", fill_price=2000.0, sl=2010.0, tp=1980.0, lots=lots)
    closed = pos.close(2010.0, 3, BASE + timedelta(minutes=15), "SL")
    # loss: fill=2000, close=2010, diff = 2000-2010 = -10 → pnl = (-10/0.01)*1.0*0.01 = -10
    assert closed.pnl_usd == pytest.approx(-10.0)


def test_bars_held():
    pos = _pos("BUY", fill_price=2000.0, sl=1990.0, tp=2020.0)
    closed = pos.close(2020.0, 7, BASE + timedelta(minutes=35), "TP")
    assert closed.bars_held == 7  # 7 - 0
