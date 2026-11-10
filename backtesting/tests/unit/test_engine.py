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


def _run(setups, candles, cfg=None, meta=None):
    with patch(
        "mt5_liquidity.backtest.engine.run_liquidity_sweep_strategy",
        return_value=setups,
    ):
        return run_backtest(
            m15_candles=[],   # irrelevant — strategy is mocked
            m5_candles=candles,
            config=cfg if cfg is not None else CFG,
            symbol_meta=meta if meta is not None else XAUUSD,
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


# --- Fix 1: position sizing uses actual fill price, not signal_price ---

def test_sizing_uses_fill_price_not_signal_price():
    """
    When the fill candle opens at a gap toward the SL, the actual SL distance
    from fill shrinks. Sizing must use fill_price so monetary risk = configured %.

    Numbers:
      BUY: signal_price=2000, sl=1990 (10-pt SL from signal)
      Fill candle gaps DOWN to 1992 (bad BUY fill — closer to SL)
      actual fill_price = 1992 (spread=0, slip=0)
      actual sl_dist   = |1992 - 1990| = 2 price units = 200 MT5 points
      risk = 10000 * 0.5% = $50
      value_per_pt/lot = 1.0 (XAUUSD)
      lots_fill_based  = floor(50 / (200 * 1.0) / 0.01) * 0.01 = floor(0.25/0.01)*0.01 = 0.25
      lots_signal_based = floor(50 / (1000 * 1.0) / 0.01) * 0.01 = 0.05

    With fill-price sizing the monetary risk is correct: 0.25 * 200pts * $1 = $50.
    """
    cfg_zero = BacktestConfig(
        risk_pct=0.5, rr_target=2.0, max_positions=1,
        spread_points=0, slippage_points=0, max_daily_loss_pct=99.0,
        initial_equity=10_000.0, commission_usd=0.0,
    )
    setup = _make_setup(
        setup_id="GAP_BUY",
        direction="BUY",
        signal_price=2000.0,
        sl_price=1990.0,
        rejection_idx=1,
    )
    candles = [_candle(i) for i in range(10)]
    # Fill candle opens at 1992 — 2 price units above sl=1990
    candles[2] = _candle(2, o=1992.0, h=2010.0, l=1991.0, c=2005.0)

    with patch("mt5_liquidity.backtest.engine.run_liquidity_sweep_strategy", return_value=[setup]):
        result = run_backtest([], candles, cfg_zero, XAUUSD)

    assert result.trades_opened == 1
    pos = result.closed_positions[0]
    assert pos.lots == pytest.approx(0.25)   # sized on actual 2-pt SL distance, not 10-pt signal


def test_gap_down_sell_sizing_uses_fill_price():
    """SELL fill candle gaps toward SL (up) — SL distance decreases → more lots."""
    # SELL: signal_price=2000, sl=2010 (10pt from signal)
    # Fill gaps UP to 2008: fill=2008, sl_dist=|2008-2010|=2pts=200 MT5pts
    # lots = floor(50/(200*1)/0.01)*0.01 = 0.25
    cfg_zero = BacktestConfig(
        risk_pct=0.5, rr_target=2.0, max_positions=1,
        spread_points=0, slippage_points=0, max_daily_loss_pct=99.0,
        initial_equity=10_000.0, commission_usd=0.0,
    )
    setup = _make_setup(
        setup_id="GAP_SELL",
        direction="SELL",
        signal_price=2000.0,
        sl_price=2010.0,
        rejection_idx=1,
    )
    candles = [_candle(i) for i in range(10)]
    candles[2] = _candle(2, o=2008.0, h=2009.0, l=1990.0, c=1995.0)

    with patch("mt5_liquidity.backtest.engine.run_liquidity_sweep_strategy", return_value=[setup]):
        result = run_backtest([], candles, cfg_zero, XAUUSD)

    assert result.trades_opened == 1
    pos = result.closed_positions[0]
    assert pos.lots == pytest.approx(0.25)


def test_min_volume_rejection_when_sl_too_wide():
    """Trade rejected when actual fill price produces SL too wide to meet volume_min."""
    # BUY: signal=2000, sl=1990 (10pt), fill gaps UP to 2060 → sl_dist=70pts=7000pts
    # lots = 50 / (7000*1) = 0.00714 → below vol_min=0.01
    cfg_zero = BacktestConfig(
        risk_pct=0.5, rr_target=2.0, max_positions=1,
        spread_points=0, slippage_points=0, max_daily_loss_pct=99.0,
        initial_equity=10_000.0, commission_usd=0.0,
    )
    setup = _make_setup(
        setup_id="REJECT",
        direction="BUY",
        signal_price=2000.0,
        sl_price=1990.0,
        rejection_idx=1,
    )
    candles = [_candle(i) for i in range(10)]
    candles[2] = _candle(2, o=2060.0, h=2080.0, l=2055.0, c=2070.0)

    with patch("mt5_liquidity.backtest.engine.run_liquidity_sweep_strategy", return_value=[setup]):
        result = run_backtest([], candles, cfg_zero, XAUUSD)

    assert result.trades_opened == 0
    assert result.setups_skipped_sizing == 1


# --- Fix 2: entry-candle SL/TP evaluation ---

def test_entry_candle_sl_hit_buy():
    """BUY position hits SL on the fill candle itself."""
    # SELL setup: rejection_idx=1 → fill at idx=2
    # We test a BUY: fill at open=2000, sl=1990, tp=2040
    # Fill candle (idx=2) dips to 1985 → SL hit immediately
    setup = _make_setup(
        direction="BUY",
        signal_price=2000.0,
        sl_price=1990.0,
        rejection_idx=1,
    )
    candles = [_candle(i) for i in range(10)]
    # Fill candle (idx=2): opens at 2000, dips to 1985 (SL=1990 hit on entry candle)
    candles[2] = _candle(2, o=2000.0, h=2002.0, l=1985.0, c=1986.0)

    cfg = BacktestConfig(
        risk_pct=1.0, rr_target=2.0, max_positions=1,
        spread_points=0, slippage_points=0, max_daily_loss_pct=99.0,
        initial_equity=10_000.0, commission_usd=0.0,
    )
    with patch("mt5_liquidity.backtest.engine.run_liquidity_sweep_strategy", return_value=[setup]):
        result = run_backtest([], candles, cfg, XAUUSD)

    assert result.trades_opened == 1
    assert result.stats.losers == 1
    pos = result.closed_positions[0]
    assert pos.exit_reason == "SL"
    assert pos.close_candle_index == 2   # closed on fill candle


def test_entry_candle_tp_hit_sell():
    """SELL position hits TP on the fill candle itself."""
    setup = _make_setup(
        direction="SELL",
        signal_price=2000.0,
        sl_price=2010.0,
        rejection_idx=1,
    )
    candles = [_candle(i) for i in range(10)]
    # Fill candle (idx=2): SELL fill=2000, sl=2010, tp=1980 (rr=2 → dist=10pts×2=20pts below fill)
    # Drop candle to 1975 → TP=1980 hit on entry candle
    candles[2] = _candle(2, o=2000.0, h=2001.0, l=1975.0, c=1980.0)

    cfg = BacktestConfig(
        risk_pct=1.0, rr_target=2.0, max_positions=1,
        spread_points=0, slippage_points=0, max_daily_loss_pct=99.0,
        initial_equity=10_000.0, commission_usd=0.0,
    )
    with patch("mt5_liquidity.backtest.engine.run_liquidity_sweep_strategy", return_value=[setup]):
        result = run_backtest([], candles, cfg, XAUUSD)

    assert result.trades_opened == 1
    assert result.stats.winners == 1
    pos = result.closed_positions[0]
    assert pos.exit_reason == "TP"
    assert pos.close_candle_index == 2   # closed on fill candle


# --- Fix 4: commission deducted in engine ---

def test_commission_deducted_from_equity():
    """Round-trip commission must reduce equity and net_pnl."""
    # SELL: rejection_idx=1, fill idx=2, TP hit at idx=3
    setup = _make_setup(signal_price=2000.0, sl_price=2010.0, rejection_idx=1)
    candles = [_candle(i) for i in range(10)]
    candles[3] = _candle(3, o=1990.0, h=1992.0, l=1975.0, c=1980.0)

    cfg_comm = BacktestConfig(
        risk_pct=1.0, rr_target=2.0, max_positions=1,
        spread_points=0, slippage_points=0, max_daily_loss_pct=99.0,
        initial_equity=10_000.0,
        commission_usd=7.0,   # $7 per lot per side → $14 round-trip per lot
    )
    with patch("mt5_liquidity.backtest.engine.run_liquidity_sweep_strategy", return_value=[setup]):
        result = run_backtest([], candles, cfg_comm, XAUUSD)

    assert result.trades_opened == 1
    pos = result.closed_positions[0]
    assert pos.commission_usd > 0
    assert pos.net_pnl_usd == pytest.approx(pos.pnl_usd - pos.commission_usd)
    assert result.stats.total_commission > 0


# --- Fix 5: daily loss with floating P&L ---

def test_daily_loss_blocked_by_floating_loss():
    """
    Open position with large floating loss must trigger the daily guard even
    before the position is realised.
    """
    # setup1: fills at idx=1 with SELL, sl hits at idx=3 (high loss)
    # setup2: fills at idx=4; daily guard should block due to floating loss at idx=4
    # We deliberately choose max_daily_loss_pct very tight (0.001%) and use a
    # setup that creates a large floating loss on the fill candle before it's closed.

    cfg = BacktestConfig(
        risk_pct=1.0,
        rr_target=2.0,
        max_positions=5,       # allow multiple — floating from s1 blocks s2
        spread_points=0,
        slippage_points=0,
        max_daily_loss_pct=0.001,   # ultra-tight: any small loss blocks
        initial_equity=10_000.0,
        commission_usd=0.0,
    )
    # s1: SELL fill at idx=1 (open=2000), sl=2010. At idx=2 price at 2001 → still open,
    # floating loss = (2000-2001)/0.01*1*lots. This is enough to breach 0.001% = $0.10.
    s1 = _make_setup("S1", direction="SELL", signal_price=2000.0, sl_price=2010.0, rejection_idx=0)
    s2 = _make_setup("S2", direction="SELL", signal_price=2000.0, sl_price=2010.0, rejection_idx=1)

    candles = [_candle(i) for i in range(10)]
    # idx=1 (fill candle for s1): opens at 2000, entry candle check → no SL/TP
    candles[1] = _candle(1, o=2000.0, h=2003.0, l=1999.0, c=2000.0)
    # idx=2 (fill candle for s2 AND the bar on which we evaluate s1 floating P&L):
    # close at 2001 → s1 (SELL) has floating loss = (2000-2001)/0.01*1*0.10 = -$10
    # daily_loss_pct = 10/10000*100 = 0.1% >> 0.001% limit → s2 blocked
    candles[2] = _candle(2, o=2000.0, h=2005.0, l=1999.0, c=2001.0)

    with patch("mt5_liquidity.backtest.engine.run_liquidity_sweep_strategy", return_value=[s1, s2]):
        result = run_backtest([], candles, cfg, XAUUSD)

    # s1 opens; s2 should be blocked by floating loss from s1
    assert result.trades_opened >= 1
    assert result.setups_skipped_daily_loss >= 1


# --- Fix 6: symbol metadata validation ---

def test_invalid_point_raises():
    bad_meta = SymbolMeta(point=0.0, tick_value=1.0, tick_size=0.01,
                          volume_min=0.01, volume_max=100.0, volume_step=0.01)
    with pytest.raises(ValueError, match="point"):
        _run([], [_candle(i) for i in range(5)], meta=bad_meta)


def test_invalid_volume_min_raises():
    bad_meta = SymbolMeta(point=0.01, tick_value=1.0, tick_size=0.01,
                          volume_min=0.0, volume_max=100.0, volume_step=0.01)
    with pytest.raises(ValueError, match="volume_min"):
        _run([], [_candle(i) for i in range(5)], meta=bad_meta)


def test_volume_min_greater_than_max_raises():
    bad_meta = SymbolMeta(point=0.01, tick_value=1.0, tick_size=0.01,
                          volume_min=50.0, volume_max=10.0, volume_step=0.01)
    with pytest.raises(ValueError, match="volume_min"):
        _run([], [_candle(i) for i in range(5)], meta=bad_meta)


def test_valid_metadata_accepted():
    # Exact XAUUSD values should pass validation
    _run([], [_candle(i) for i in range(5)], meta=XAUUSD)


def test_trading_day_timezone_accepts_non_utc_zone():
    cfg = BacktestConfig(**{**CFG.__dict__, "trading_day_timezone": "America/New_York"})
    candle = Candle(timestamp=datetime(2025, 1, 7, 0, 30, tzinfo=timezone.utc),
                    open=2000.0, high=2001.0, low=1999.0, close=2000.0)
    result = _run([], [candle], cfg=cfg)
    assert result.stats.total_trades == 0


def test_invalid_trading_day_timezone_fails_fast():
    cfg = BacktestConfig(**{**CFG.__dict__, "trading_day_timezone": "Not/A_Real_Zone"})
    with pytest.raises(ValueError, match="trading_day_timezone"):
        _run([], [_candle(0)], cfg=cfg)
