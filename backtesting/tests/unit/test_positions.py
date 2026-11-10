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


# --- Fix 2: entry-candle SL/TP via check_entry_candle ---

def _pos_with_spread(direction, fill_price, sl, tp, lots=0.01, spread_points=0):
    return OpenPosition(
        fill=_fill(direction, fill_price, sl, tp, lots),
        value_per_point_per_lot=VPP,
        point=POINT,
        spread_points=spread_points,
    )


def test_entry_candle_buy_sl_hit():
    pos = _pos("BUY", fill_price=2000.0, sl=1990.0, tp=2020.0)
    # Candle goes as low as 1985 — SL should trigger
    candle = _candle(2000.0, 2005.0, 1985.0, 1986.0)
    status, close_price = pos.check_entry_candle(candle)
    assert status == PositionStatus.HIT_SL
    assert close_price == pytest.approx(1990.0)


def test_entry_candle_buy_tp_hit():
    pos = _pos("BUY", fill_price=2000.0, sl=1990.0, tp=2020.0)
    candle = _candle(2000.0, 2025.0, 1998.0, 2022.0)
    status, close_price = pos.check_entry_candle(candle)
    assert status == PositionStatus.HIT_TP
    assert close_price == pytest.approx(2020.0)


def test_entry_candle_buy_both_hit_sl_wins():
    pos = _pos("BUY", fill_price=2000.0, sl=1990.0, tp=2020.0)
    candle = _candle(2000.0, 2025.0, 1985.0, 2000.0)
    status, close_price = pos.check_entry_candle(candle)
    assert status == PositionStatus.HIT_SL


def test_entry_candle_buy_neither_hit():
    pos = _pos("BUY", fill_price=2000.0, sl=1990.0, tp=2020.0)
    candle = _candle(2000.0, 2010.0, 1995.0, 2005.0)
    status, _ = pos.check_entry_candle(candle)
    assert status == PositionStatus.OPEN


def test_entry_candle_sell_sl_hit():
    pos = _pos("SELL", fill_price=2000.0, sl=2010.0, tp=1980.0)
    candle = _candle(1999.0, 2015.0, 1998.0, 2012.0)
    status, close_price = pos.check_entry_candle(candle)
    assert status == PositionStatus.HIT_SL
    assert close_price == pytest.approx(2010.0)


def test_entry_candle_sell_tp_hit():
    pos = _pos("SELL", fill_price=2000.0, sl=2010.0, tp=1980.0)
    candle = _candle(1999.0, 2000.0, 1975.0, 1990.0)
    status, close_price = pos.check_entry_candle(candle)
    assert status == PositionStatus.HIT_TP
    assert close_price == pytest.approx(1980.0)


def test_entry_candle_sell_neither_hit():
    pos = _pos("SELL", fill_price=2000.0, sl=2010.0, tp=1980.0)
    candle = _candle(1999.0, 2005.0, 1990.0, 2000.0)
    status, _ = pos.check_entry_candle(candle)
    assert status == PositionStatus.OPEN


# --- Fix 3: SELL ask-side exit model with non-zero spread ---

def test_sell_sl_hit_requires_ask_to_reach_level():
    # SELL: sl=2010, spread=10pts=0.10. SL triggers when bid+0.10 >= 2010 → bid >= 2009.90
    pos = _pos_with_spread("SELL", fill_price=2000.0, sl=2010.0, tp=1980.0, spread_points=10)
    # bid_high=2009.80 → ask_high=2009.80+0.10=2009.90 < 2010 → NOT triggered
    candle = _candle(2000.0, 2009.80, 1998.0, 2000.0)
    status, _ = pos.check_candle(candle, 1)
    assert status == PositionStatus.OPEN


def test_sell_sl_hit_when_ask_reaches_level():
    # bid_high=2009.90 → ask_high=2009.90+0.10=2010 ≥ 2010 → TRIGGERED
    pos = _pos_with_spread("SELL", fill_price=2000.0, sl=2010.0, tp=1980.0, spread_points=10)
    candle = _candle(2000.0, 2009.90, 1998.0, 2000.0)
    status, close_price = pos.check_candle(candle, 1)
    assert status == PositionStatus.HIT_SL
    assert close_price == pytest.approx(2010.0)   # exit at sl (ask level)


def test_sell_pnl_includes_spread_cost():
    # SELL: tp=1980 (in bid terms, spread=10pts=0.10).
    # With spread: TP triggers when ask <= 1980, i.e., bid+0.10 <= 1980, i.e., bid <= 1979.90.
    # At bid_low=1979.95:
    #   no-spread: 1979.95 <= 1980 → HIT_TP
    #   with-spread: 1979.95+0.10=1980.05 > 1980 → OPEN
    # This demonstrates that spread delays the TP trigger, correctly modelling ask-side exits.
    pos_spread    = _pos_with_spread("SELL", fill_price=2000.0, sl=2010.0, tp=1980.0, spread_points=10)
    pos_no_spread = _pos_with_spread("SELL", fill_price=2000.0, sl=2010.0, tp=1980.0, spread_points=0)

    candle = _candle(1999.0, 2000.0, 1979.95, 1990.0)   # bid_low = 1979.95

    st_sp, _ = pos_spread.check_candle(candle, 1)
    st_ns, _ = pos_no_spread.check_candle(candle, 1)

    assert st_sp == PositionStatus.OPEN    # spread version: ask_low=1980.05 > tp=1980 → not triggered
    assert st_ns == PositionStatus.HIT_TP  # no-spread: bid_low=1979.95 <= 1980 → triggered


def test_sell_gap_close_includes_spread():
    # Gap: bid_open=2020 > sl=2010 → exit at ask_open = 2020 + 0.10 = 2020.10
    pos = _pos_with_spread("SELL", fill_price=2000.0, sl=2010.0, tp=1980.0, spread_points=10)
    candle = _candle(2020.0, 2025.0, 2015.0, 2018.0)
    status, close_price = pos.check_candle(candle, 1)
    assert status == PositionStatus.HIT_SL
    assert close_price == pytest.approx(2020.0 + 10 * POINT)


def test_buy_exit_unaffected_by_spread_points():
    # BUY exits at bid regardless of spread_points setting
    pos_spread   = _pos_with_spread("BUY", fill_price=2000.0, sl=1990.0, tp=2020.0, spread_points=20)
    pos_no_spread = _pos_with_spread("BUY", fill_price=2000.0, sl=1990.0, tp=2020.0, spread_points=0)
    candle = _candle(2001.0, 2025.0, 1998.0, 2022.0)
    st1, cp1 = pos_spread.check_candle(candle, 1)
    st2, cp2 = pos_no_spread.check_candle(candle, 1)
    assert st1 == st2 == PositionStatus.HIT_TP
    assert cp1 == cp2 == pytest.approx(2020.0)   # BUY exits at bid (not affected by spread_points)


# --- Fix 4: commission on ClosedPosition ---

def test_commission_zero_gives_net_pnl_equal_gross():
    pos = _pos("BUY", fill_price=2000.0, sl=1990.0, tp=2020.0)
    closed = pos.close(2020.0, 5, BASE + timedelta(minutes=25), "TP", commission_usd=0.0)
    assert closed.commission_usd == pytest.approx(0.0)
    assert closed.net_pnl_usd   == pytest.approx(closed.pnl_usd)


def test_commission_deducted_from_net_pnl():
    pos = _pos("BUY", fill_price=2000.0, sl=1990.0, tp=2020.0, lots=0.1)
    closed = pos.close(2020.0, 5, BASE + timedelta(minutes=25), "TP", commission_usd=5.0)
    # gross P&L = (20/0.01) * 1.0 * 0.1 = 200. Net = 200 - 5 = 195
    assert closed.pnl_usd     == pytest.approx(200.0)
    assert closed.commission_usd == pytest.approx(5.0)
    assert closed.net_pnl_usd == pytest.approx(195.0)


def test_commission_on_losing_trade_increases_loss():
    pos = _pos("SELL", fill_price=2000.0, sl=2010.0, tp=1980.0, lots=0.01)
    closed = pos.close(2010.0, 3, BASE + timedelta(minutes=15), "SL", commission_usd=2.0)
    assert closed.pnl_usd     == pytest.approx(-10.0)
    assert closed.net_pnl_usd == pytest.approx(-12.0)


def test_commission_scales_with_lots():
    # commission passed in is already round-trip; just stored
    pos_small = _pos("BUY", fill_price=2000.0, sl=1990.0, tp=2020.0, lots=0.01)
    pos_large = _pos("BUY", fill_price=2000.0, sl=1990.0, tp=2020.0, lots=0.10)

    closed_small = pos_small.close(2020.0, 5, BASE + timedelta(minutes=25), "TP", commission_usd=0.07)
    closed_large = pos_large.close(2020.0, 5, BASE + timedelta(minutes=25), "TP", commission_usd=0.70)

    assert closed_small.commission_usd == pytest.approx(0.07)
    assert closed_large.commission_usd == pytest.approx(0.70)


# --- unrealized_pnl ---

def test_unrealized_pnl_buy_no_spread():
    pos = _pos("BUY", fill_price=2000.0, sl=1990.0, tp=2020.0, lots=0.01)
    from mt5_liquidity.data.candles import Candle
    candle = Candle(timestamp=BASE, open=2005.0, high=2010.0, low=2003.0, close=2008.0)
    # unrealised = (2008 - 2000) / 0.01 * 1.0 * 0.01 = 8
    assert pos.unrealized_pnl(candle) == pytest.approx(8.0)


def test_unrealized_pnl_sell_includes_spread():
    # SELL fill=2000, spread=10pts=0.10, mark = close + spread = 2005 + 0.10 = 2005.10
    # pnl = (2000 - 2005.10) / 0.01 * 1.0 * 0.01 = -5.10
    pos = _pos_with_spread("SELL", fill_price=2000.0, sl=2010.0, tp=1980.0, lots=0.01, spread_points=10)
    from mt5_liquidity.data.candles import Candle
    candle = Candle(timestamp=BASE, open=2000.0, high=2005.0, low=1998.0, close=2005.0)
    assert pos.unrealized_pnl(candle) == pytest.approx(-5.10)


def test_entry_candle_ambiguous_buy_bar_uses_stop_first():
    pos = _pos("BUY", fill_price=2000.0, sl=1990.0, tp=2020.0)
    candle = _candle(2000.0, 2025.0, 1985.0, 2005.0)
    status, close_price = pos.check_entry_candle(candle)
    assert status == PositionStatus.HIT_SL
    assert close_price == pytest.approx(1990.0)


def test_entry_candle_ambiguous_sell_bar_uses_stop_first_with_spread():
    pos = OpenPosition(fill=_fill("SELL", 2000.0, 2010.0, 1980.0),
                       value_per_point_per_lot=VPP, point=POINT, spread_points=2)
    candle = _candle(2000.0, 2011.0, 1978.0, 2000.0)
    status, close_price = pos.check_entry_candle(candle)
    assert status == PositionStatus.HIT_SL
    assert close_price == pytest.approx(2010.0)
