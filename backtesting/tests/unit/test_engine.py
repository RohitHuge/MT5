"""Unit tests for the backtest engine (backtest/engine.py).

These tests inject synthetic setups by patching run_liquidity_sweep_strategy
so we can test the fill/close/risk logic without needing real OHLC data
that actually produces strategy signals.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from mt5_liquidity.backtest.engine import BacktestResult, SymbolMeta, run_backtest
from mt5_liquidity.backtest.positions import ClosedPosition
from mt5_liquidity.config import BacktestConfig
from mt5_liquidity.data.candles import Candle
from mt5_liquidity.strategy.setup import Setup, SetupState
from mt5_liquidity.strategy.retests import RetestSignal
from mt5_liquidity.strategy.sweeps import SweepSignal
from mt5_liquidity.strategy.models import ZoneType

BASE = datetime(2025, 1, 6, 0, 0, tzinfo=timezone.utc)
M5 = timedelta(minutes=5)

XAUUSD = SymbolMeta(
    point=0.01,
    tick_value=1.0,
    tick_size=0.01,
    volume_min=0.01,
    volume_max=100.0,
    volume_step=0.01,
)

CFG = BacktestConfig(
    risk_pct=1.0,
    rr_target=2.0,
    max_positions=1,
    spread_points=0,
    slippage_points=0,
    max_daily_loss_pct=99.0,
    initial_equity=10_000.0,
)


def _candle(i, o=2000.0, h=None, l=None, c=None):
    h = h if h is not None else o + 5
    l = l if l is not None else o - 5
    c = c if c is not None else o
    return Candle(timestamp=BASE + i * M5, open=o, high=h, low=l, close=c)


def _make_sweep(zone_id="Z1", sweep_idx=0, zone_type=ZoneType.BSL, wick=2010.0):
    return SweepSignal(
        zone_id=zone_id,
        zone_type=zone_type,
        sweep_candle_index=sweep_idx,
        sweep_candle_ts=BASE + sweep_idx * M5,
        wick_extreme=wick,
        close_price=2000.0,
        zone_upper=2012.0,
        zone_lower=2008.0,
        min_sweep_points=5,
    )


def _make_retest(rejection_idx=1, signal_price=2000.0, direction="BEARISH"):
    return RetestSignal(
        mss_zone_id="Z1",
        direction=direction,
        structure_level=2000.0,
        retest_band_upper=2001.0,
        retest_band_lower=1999.0,
        rejection_candle_index=rejection_idx,
        rejection_candle_ts=BASE + rejection_idx * M5,
        rejection_candle_close=signal_price,
        rejection_candle_open=signal_price + 1,
        signal_price=signal_price,
        bars_after_mss=2,
    )


def _make_setup(
    setup_id="S1",
    direction="SELL",
    signal_price=2000.0,
    sl_price=2010.0,
    rejection_idx=1,
):
    s = Setup(
        setup_id=setup_id,
        direction=direction,
        sweep=_make_sweep(),
        retest=_make_retest(rejection_idx=rejection_idx, signal_price=signal_price),
        state=SetupState.ENTRY_READY,
        signal_price=signal_price,
        stop_loss_price=sl_price,
    )
    return s


def _run(setups, candles):
    with patch(
        "mt5_liquidity.backtest.engine.run_liquidity_sweep_strategy",
        return_value=setups,
    ):
        return run_backtest(
            m15_candles=[],   # irrelevant — strategy is mocked
            m5_candles=candles,
            config=CFG,
            symbol_meta=XAUUSD,
        )


def test_no_setups_returns_empty():
    candles = [_candle(i) for i in range(5)]
    result = _run([], candles)
    assert result.trades_opened == 0
    assert result.stats.total_trades == 0


def test_sell_tp_hit():
    # signal at idx=1, fill at idx=2 (open=2000)
    # SELL: fill=2000-0=2000, sl=2010, tp=2000-10*2=1980
    candles = [_candle(i) for i in range(10)]
    # Make idx=3 hit TP: low <= 1980
    candles[3] = _candle(3, o=1990.0, h=1992.0, l=1975.0, c=1980.0)

    setup = _make_setup(signal_price=2000.0, sl_price=2010.0, rejection_idx=1)
    result = _run([setup], candles)

    assert result.trades_opened == 1
    assert result.stats.winners == 1
    assert result.stats.net_pnl > 0


def test_buy_sl_hit():
    # BUY: signal_idx=1, fill_idx=2 open=2000, sl=1990, tp=2020
    setup = _make_setup(
        setup_id="BUY_SL", direction="BUY",
        signal_price=2000.0, sl_price=1990.0,
        rejection_idx=1,
    )
    candles = [_candle(i) for i in range(10)]
    candles[3] = _candle(3, o=1995.0, h=1996.0, l=1985.0, c=1986.0)

    result = _run([setup], candles)
    assert result.trades_opened == 1
    assert result.stats.losers == 1
    assert result.stats.net_pnl < 0


def test_max_positions_blocks_second_entry():
    cfg = BacktestConfig(**{**CFG.__dict__, "max_positions": 1})
    setup1 = _make_setup("S1", rejection_idx=1)
    setup2 = _make_setup("S2", rejection_idx=2)

    candles = [_candle(i) for i in range(20)]

    with patch("mt5_liquidity.backtest.engine.run_liquidity_sweep_strategy", return_value=[setup1, setup2]):
        result = run_backtest([], candles, cfg, XAUUSD)

    # First setup fills; second is blocked while first is open
    assert result.setups_skipped_max_pos >= 1


def test_daily_loss_limit_blocks_entry():
    # Setup1: rejection_idx=0 → fill at idx=1 (open=2000); SELL sl=2010 tp=1980
    # Candle 2: h=2015 → SL hit → loss of $100 on 0.1 lot
    # max_daily_loss_pct=0.001 (0.001%) → $1.00 trigger; loss $100 >> trigger
    # Setup2: rejection_idx=3 → fill at idx=4; should be blocked by daily loss guard
    cfg = BacktestConfig(**{**CFG.__dict__, "max_daily_loss_pct": 0.001, "max_positions": 5, "risk_pct": 1.0})
    setup1 = _make_setup("S_DAILY1", direction="SELL", signal_price=2000.0, sl_price=2010.0, rejection_idx=0)
    setup2 = _make_setup("S_DAILY2", direction="SELL", signal_price=2000.0, sl_price=2010.0, rejection_idx=3)

    candles = [_candle(i) for i in range(10)]
    # Candle 2: hits SELL SL (high >= 2010)
    candles[2] = _candle(2, o=2005.0, h=2015.0, l=2004.0, c=2010.0)

    with patch("mt5_liquidity.backtest.engine.run_liquidity_sweep_strategy", return_value=[setup1, setup2]):
        result = run_backtest([], candles, cfg, XAUUSD)

    # Setup1 should have opened and been stopped out; setup2 should be blocked
    assert result.trades_opened >= 1
    assert result.setups_skipped_daily_loss >= 1


def test_force_close_at_end_of_data():
    # Signal at last-1; fill would be last candle; no close bar → force-close
    n = 5
    candles = [_candle(i) for i in range(n)]
    setup = _make_setup(rejection_idx=n - 2)

    result = _run([setup], candles)
    assert result.force_closed >= 1 or result.stats.total_trades >= 1
