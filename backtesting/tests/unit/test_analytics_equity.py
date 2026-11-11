"""Mark-to-market equity analytics tests.

These tests run the real engine (with synthetic setups patched in, the same
technique as test_engine.py) and verify:
- end-of-candle MTM observations including floating P&L,
- spread-aware BUY/SELL valuation,
- drawdown during open trades and recovery,
- final-equity reconciliation with the ledger,
- that the MTM drawdown can differ from the closed-trade drawdown,
- no future-data leakage (each observation uses only its own candle).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from mt5_liquidity.backtest.engine import SymbolMeta, run_backtest
from mt5_liquidity.config import BacktestConfig
from mt5_liquidity.data.candles import Candle
from mt5_liquidity.reporting.analytics.aggregation import max_drawdown_from_series
from mt5_liquidity.reporting.analytics.equity import analyze_mark_to_market
from mt5_liquidity.reporting.analytics.models import DrawdownSummary
from mt5_liquidity.strategy.models import ZoneType
from mt5_liquidity.strategy.retests import RetestSignal
from mt5_liquidity.strategy.setup import Setup, SetupState
from mt5_liquidity.strategy.sweeps import SweepSignal

UTC = timezone.utc
BASE = datetime(2026, 11, 9, 0, 0, tzinfo=UTC)   # Monday
M5 = timedelta(minutes=5)

XAUUSD = SymbolMeta(point=0.01, tick_value=1.0, tick_size=0.01,
                    volume_min=0.01, volume_max=100.0, volume_step=0.01)


def _candle(i, o=2000.0, h=None, l=None, c=None):
    h = h if h is not None else o + 2
    l = l if l is not None else o - 2
    c = c if c is not None else o
    return Candle(timestamp=BASE + i * M5, open=o, high=h, low=l, close=c)


def _make_setup(setup_id, direction, signal_price=2000.0, sl_price=2010.0, rejection_idx=1):
    sweep = SweepSignal(
        zone_id="Z1", zone_type=ZoneType.BSL if direction == "SELL" else ZoneType.SSL,
        sweep_candle_index=0, sweep_candle_ts=BASE,
        wick_extreme=2012.0 if direction == "SELL" else 1988.0,
        close_price=2000.0, zone_upper=2012.0, zone_lower=1988.0,
        min_sweep_points=5,
    )
    retest = RetestSignal(
        mss_zone_id="Z1", direction="BEARISH" if direction == "SELL" else "BULLISH",
        structure_level=2000.0, retest_band_upper=2001.0, retest_band_lower=1999.0,
        rejection_candle_index=rejection_idx,
        rejection_candle_ts=BASE + rejection_idx * M5,
        rejection_candle_close=signal_price, rejection_candle_open=signal_price,
        signal_price=signal_price, bars_after_mss=2,
    )
    return Setup(setup_id=setup_id, direction=direction, sweep=sweep,
                 retest=retest, state=SetupState.ENTRY_READY,
                 signal_price=signal_price, stop_loss_price=sl_price)


def _run(setups, candles, cfg):
    with patch("mt5_liquidity.backtest.engine.run_liquidity_sweep_strategy",
               return_value=setups):
        return run_backtest([], candles, cfg, XAUUSD)


def _flat_cfg(**overrides) -> BacktestConfig:
    defaults = dict(
        risk_pct=1.0, rr_target=2.0, max_positions=1,
        spread_points=0, slippage_points=0, max_daily_loss_pct=99.0,
        initial_equity=10_000.0, commission_usd=0.0,
    )
    defaults.update(overrides)
    return BacktestConfig(**defaults)


class TestNoOpenPositions:
    def test_no_trades_curve_equals_initial_equity(self):
        candles = [_candle(i) for i in range(5)]
        result = _run([], candles, _flat_cfg())
        assert len(result.mark_to_market_curve) == 5
        for point in result.mark_to_market_curve:
            assert point.realised_equity == pytest.approx(10_000.0)
            assert point.floating_pnl == 0.0
            assert point.mtm_equity == pytest.approx(10_000.0)
            assert point.open_positions == 0

    def test_no_candles_empty_curve(self):
        result = _run([], [], _flat_cfg())
        assert result.mark_to_market_curve == []
        assert result.stats.final_equity == pytest.approx(10_000.0)


class TestBuyFloatingValuation:
    def test_floating_loss_then_recovery_then_tp(self):
        """MTM drawdown while the BUY is open, then recovery, then TP close.

        The closed-trade equity curve never dips below 10_000 (the loss is
        realised only at the TP close, which is a gain) so the closed-trade
        drawdown is 0 while the MTM drawdown is 50 — they must differ.
        """
        candles = [
            _candle(0),                                            # no position
            _candle(1, o=2000, h=2002, l=1999, c=2000),            # fill bar
            _candle(2, o=2000, h=2005, l=1995, c=1995),            # floating -50
            _candle(3, o=2010, h=2015, l=2005, c=2015),            # floating +150
            _candle(4, o=2016, h=2025, l=2010, c=2020),            # TP hit
        ]
        setup = _make_setup("MTM_BUY", "BUY", sl_price=1990.0, rejection_idx=1)
        result = _run([setup], candles, _flat_cfg())

        assert result.trades_opened == 1
        curve = result.mark_to_market_curve
        assert len(curve) == 5

        # BUY marks at bid close: floating at idx2 = (1995-2000)/0.01*0.1 = -50
        p2 = curve[2]
        assert p2.open_positions == 1
        assert p2.floating_pnl == pytest.approx(-50.0)
        assert p2.mtm_equity == pytest.approx(9_950.0)
        # floating at idx3 = (2015-2000)/0.01*0.1 = +150
        assert curve[3].floating_pnl == pytest.approx(150.0)
        assert curve[3].mtm_equity == pytest.approx(10_150.0)

        dd = analyze_mark_to_market(
            [(p.timestamp, p.realised_equity, p.floating_pnl,
              p.mtm_equity, p.open_positions) for p in curve])
        assert dd.max_drawdown_usd == pytest.approx(50.0)
        assert dd.peak_ts == curve[0].timestamp    # first 10_000 observation
        assert dd.trough_ts == curve[2].timestamp
        assert dd.recovery_ts == curve[3].timestamp

        # Closed-trade equity series (single point at the TP close, above the
        # start) has zero drawdown; the MTM drawdown of 50 must differ.
        closed_dd = max_drawdown_from_series(result.equity_curve).max_drawdown_usd
        assert closed_dd == 0.0
        assert dd.max_drawdown_usd > closed_dd

        # Final reconciliation
        assert curve[-1].floating_pnl == 0.0
        assert curve[-1].mtm_equity == pytest.approx(result.stats.final_equity)

    def test_no_lookahead_each_point_uses_own_candle_only(self):
        """If any observation used later candles, idx2 floating would not
        equal the mark from candle idx2's close alone."""
        candles = [
            _candle(0),
            _candle(1, o=2000, h=2002, l=1999, c=2000),
            _candle(2, o=2000, h=2008, l=1996, c=1996),
            _candle(3, o=2000, h=2002, l=1998, c=2000),
        ]
        setup = _make_setup("MTM_NOLA", "BUY", sl_price=1990.0, rejection_idx=1)
        result = _run([setup], candles, _flat_cfg())
        p2 = result.mark_to_market_curve[2]
        expected = (candles[2].close - 2000.0) / 0.01 * 1.0 * result.closed_positions[0].lots
        assert p2.floating_pnl == pytest.approx(expected)


class TestSellFloatingValuation:
    def test_sell_marked_at_ask_close_spread_visible(self):
        """SELL floating P&L must include the ask-side spread at the close."""
        cfg = _flat_cfg(spread_points=21)
        candles = [_candle(i, o=2000, h=2001, l=1999, c=2000) for i in range(4)]
        setup = _make_setup("MTM_SELL", "SELL", sl_price=2010.0, rejection_idx=1)
        result = _run([setup], candles, cfg)

        assert result.trades_opened == 1
        p2 = result.mark_to_market_curve[2]   # fill bar is rejection_idx + 1 = 2
        lots = result.closed_positions[0].lots
        # SELL mark = bid close + spread = 2000.21 → floating = (2000-2000.21)/0.01*lots
        expected = (2000.0 - 2000.21) / 0.01 * 1.0 * lots
        assert p2.open_positions == 1
        assert p2.floating_pnl == pytest.approx(expected)
        assert p2.floating_pnl < 0     # spread cost visible
        assert p2.mtm_equity == pytest.approx(p2.realised_equity + p2.floating_pnl)


class TestMultipleOpenPositions:
    def test_two_open_positions_floating_summed(self):
        cfg = _flat_cfg(max_positions=2)
        candles = [
            _candle(0),
            _candle(1, o=2000, h=2002, l=1999, c=2000),   # SELL fills here
            _candle(2, o=2000, h=2002, l=1999, c=2003),   # BUY fills here
        ]
        sell = _make_setup("MTM_S2", "SELL", sl_price=2010.0, rejection_idx=0)
        buy = _make_setup("MTM_B2", "BUY", sl_price=1990.0, rejection_idx=1)
        result = _run([sell, buy], candles, cfg)

        assert result.trades_opened == 2
        p2 = result.mark_to_market_curve[2]
        assert p2.open_positions == 2
        sell_lots = result.closed_positions[0].lots   # SELL recorded first
        buy_lots = result.closed_positions[1].lots
        sell_floating = (2000.0 - candles[2].close) / 0.01 * 1.0 * sell_lots
        buy_floating = (candles[2].close - 2000.0) / 0.01 * 1.0 * buy_lots
        assert p2.floating_pnl == pytest.approx(sell_floating + buy_floating)


class TestForceCloseReconciliation:
    def test_final_point_reconciles_after_force_close(self):
        candles = [_candle(i, o=2000, h=2001, l=1999, c=2000) for i in range(6)]
        setup = _make_setup("MTM_FC", "SELL", sl_price=2010.0, rejection_idx=1)
        result = _run([setup], candles, _flat_cfg())

        assert result.force_closed == 1
        final = result.mark_to_market_curve[-1]
        assert final.open_positions == 0
        assert final.floating_pnl == 0.0
        assert final.mtm_equity == pytest.approx(result.stats.final_equity)
        assert final.realised_equity == pytest.approx(result.stats.final_equity)

    def test_mtmdrawdown_on_direct_points(self):
        points = [
            (BASE, 10_000.0, 0.0, 10_000.0, 0),
            (BASE + M5, 10_000.0, -100.0, 9_900.0, 1),
            (BASE + 2 * M5, 10_000.0, 50.0, 10_050.0, 1),
        ]
        dd = analyze_mark_to_market(points)
        assert dd.max_drawdown_usd == pytest.approx(100.0)
        assert dd.recovery_ts == BASE + 2 * M5


class TestDrawdownSummaryEmpty:
    def test_empty_points(self):
        dd = analyze_mark_to_market([])
        assert dd == DrawdownSummary()
        assert dd.max_drawdown_usd == 0.0
