from datetime import datetime, timezone
import pytest

from mt5_liquidity.backtest.execution import FillResult
from mt5_liquidity.backtest.positions import OpenPosition, PositionStatus
from mt5_liquidity.data.candles import Candle


TS = datetime(2025, 1, 1, tzinfo=timezone.utc)
POINT = 0.01


def make_position(direction, exit_slippage_points):
    fill = FillResult(
        setup_id="SLIP", direction=direction, fill_price=2000.0,
        sl_price=1990.0 if direction == "BUY" else 2010.0,
        tp_price=2020.0 if direction == "BUY" else 1980.0,
        lots=0.1, fill_candle_index=0, fill_candle_ts=TS,
    )
    return OpenPosition(
        fill=fill, value_per_point_per_lot=1.0, point=POINT,
        spread_points=10, exit_slippage_points=exit_slippage_points,
    )


def candle(open_, high, low, close):
    return Candle(timestamp=TS, open=open_, high=high, low=low, close=close)


def test_buy_exit_slippage_reduces_executable_bid_and_pnl():
    pos = make_position("BUY", 5)
    assert pos.executable_exit_price(2005.0) == pytest.approx(2004.95)
    assert pos.unrealized_pnl(candle(2005, 2006, 2004, 2005)) == pytest.approx(49.5)


def test_sell_exit_slippage_increases_executable_ask_and_cost():
    pos = make_position("SELL", 5)
    # Ask = bid close + 0.10 spread + 0.05 adverse slippage.
    assert pos.executable_exit_price(1995.0) == pytest.approx(1995.15)
    assert pos.unrealized_pnl(candle(1995, 1996, 1994, 1995)) == pytest.approx(48.5)


def test_buy_stop_is_triggered_using_slippage_adjusted_exit_price():
    pos = make_position("BUY", 5)
    # The executable bid reaches 1989.99; the stop exit then incurs another
    # adverse 0.05 price-unit fill slippage, so the simulated fill is 1989.95.
    status, price = pos.check_candle(candle(1995, 2000, 1990.04, 1995), 1)
    assert status == PositionStatus.HIT_SL
    assert price == pytest.approx(1989.95)
