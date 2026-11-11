"""Phase 4 Milestone 2 tests: intratrade MAE/MFE analytics.

Conventions under test (matching the engine's exit valuation):
- Bar extremes are converted with executable_exit_price (SELL excursions
  include the ask-side spread; both sides include exit slippage).
- MAE <= 0, MFE >= 0, clamped at 0 for one-sided trades.
- The actual exit price is part of the excursion path, so the realised
  outcome is always within [mae_usd, mfe_usd].
- R-normalisation uses the fill-to-SL distance priced for the trade's volume.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from mt5_liquidity.backtest.engine import SymbolMeta, run_backtest
from mt5_liquidity.backtest.execution import FillResult
from mt5_liquidity.backtest.ledger import TradeLedger
from mt5_liquidity.backtest.positions import ClosedPosition, OpenPosition
from mt5_liquidity.config import AccountRiskConfig, BacktestConfig
from mt5_liquidity.data.candles import Candle
from mt5_liquidity.reporting.analytics.aggregation import aggregate_trade_stats
from mt5_liquidity.reporting.exporter import export_backtest_report
from mt5_liquidity.strategy.models import ZoneType
from mt5_liquidity.strategy.retests import RetestSignal
from mt5_liquidity.strategy.setup import Setup, SetupState
from mt5_liquidity.strategy.sweeps import SweepSignal

UTC = timezone.utc
BASE = datetime(2026, 11, 9, 0, 0, tzinfo=UTC)
M5 = timedelta(minutes=5)
XAUUSD = SymbolMeta(point=0.01, tick_value=1.0, tick_size=0.01,
                    volume_min=0.01, volume_max=100.0, volume_step=0.01)


def _candle(i, o=2000.0, h=None, l=None, c=None):
    h = h if h is not None else o + 2
    l = l if l is not None else o - 2
    c = c if c is not None else o
    return Candle(timestamp=BASE + i * M5, open=o, high=h, low=l, close=c)


def _fill(direction="BUY", fill_price=2000.0, sl_price=1990.0, lots=0.1):
    tp = fill_price + (fill_price - sl_price) * 2 if direction == "BUY" \
        else fill_price - (sl_price - fill_price) * 2
    return FillResult(setup_id="MAE_T", direction=direction,
                      fill_price=fill_price, sl_price=sl_price, tp_price=tp,
                      lots=lots, fill_candle_index=1,
                      fill_candle_ts=BASE + M5)


class TestBuyExcursions:
    def test_bar_extremes_with_zero_friction(self):
        pos = OpenPosition(fill=_fill("BUY"), value_per_point_per_lot=1.0,
                           point=0.01)
        pos.check_candle(_candle(2, o=1999, h=2008, l=1995, c=2005), 2)
        # adverse: exit(1995) - 2000 = -5.00 -> -$50 at 0.1 lots
        # favourable: exit(2008) - 2000 = +8.00 -> +$80
        assert pos.mae_usd == pytest.approx(-50.0)
        assert pos.mfe_usd == pytest.approx(80.0)

    def test_closed_values_and_r_normalisation(self):
        pos = OpenPosition(fill=_fill("BUY"), value_per_point_per_lot=1.0,
                           point=0.01)
        pos.check_candle(_candle(2, o=1999, h=2008, l=1995, c=2005), 2)
        closed = pos.close(2001.0, 3, BASE + 2 * M5, "FORCE_CLOSE")
        # realised +100 keeps mae/mfe from the bar path; 1R = 1000 pts * 0.1 = $100
        assert closed.mae_usd == pytest.approx(-50.0)
        assert closed.mfe_usd == pytest.approx(80.0)
        assert closed.mae_r == pytest.approx(-0.5)
        assert closed.mfe_r == pytest.approx(0.8)

    def test_exit_price_included_in_path(self):
        """A force-close beyond all observed extremes extends the excursion."""
        pos = OpenPosition(fill=_fill("BUY"), value_per_point_per_lot=1.0,
                           point=0.01)
        pos.check_candle(_candle(2, o=2000, h=2002, l=1999, c=2001), 2)
        closed = pos.close(2015.0, 9, BASE + 9 * M5, "FORCE_CLOSE")
        assert closed.mfe_usd == pytest.approx(150.0)   # exit(2015)-2000 = +15
        closed2 = OpenPosition(fill=_fill("BUY"), value_per_point_per_lot=1.0,
                               point=0.01).close(1985.0, 9, BASE + 9 * M5, "FORCE_CLOSE")
        assert closed2.mae_usd == pytest.approx(-150.0)

    def test_one_sided_bar_is_clamped(self):
        """A bar entirely above the BUY fill creates no negative MAE."""
        pos = OpenPosition(fill=_fill("BUY"), value_per_point_per_lot=1.0,
                           point=0.01)
        pos.check_candle(_candle(2, o=2005, h=2008, l=2005, c=2006), 2)
        assert pos.mae_usd == 0.0
        assert pos.mfe_usd == pytest.approx(80.0)

    def test_zero_excursion_trade(self):
        pos = OpenPosition(fill=_fill("BUY"), value_per_point_per_lot=1.0,
                           point=0.01)
        pos.check_candle(_candle(2, o=2000, h=2000, l=2000, c=2000), 2)
        closed = pos.close(2000.0, 5, BASE + 5 * M5, "FORCE_CLOSE")
        assert closed.mae_usd == 0.0
        assert closed.mfe_usd == 0.0
        assert closed.mae_r == 0.0 and closed.mfe_r == 0.0


class TestSellExcursions:
    def test_ask_side_spread_included(self):
        """SELL exits at ask = bid + spread: excursions carry the spread."""
        pos = OpenPosition(fill=_fill("SELL", sl_price=2010.0),
                           value_per_point_per_lot=1.0, point=0.01,
                           spread_points=21)
        pos.check_candle(_candle(2, o=2000, h=2005, l=1995, c=2000), 2)
        # adverse: 2000 - exit(2005) = 2000 - 2005.21 = -5.21 -> -$52.10
        # favourable: 2000 - exit(1995) = 2000 - 1995.21 = +4.79 -> +$47.90
        assert pos.mae_usd == pytest.approx(-52.10)
        assert pos.mfe_usd == pytest.approx(47.90)
        closed = pos.close(2001.0, 3, BASE + 2 * M5, "FORCE_CLOSE")
        # 1R = (2010-2000)/0.01 * 0.1 = $100
        assert closed.mae_r == pytest.approx(-0.521)
        assert closed.mfe_r == pytest.approx(0.479)

    def test_exit_slippage_applies_to_both_extremes(self):
        pos = OpenPosition(fill=_fill("BUY"), value_per_point_per_lot=1.0,
                           point=0.01, exit_slippage_points=3)
        pos.check_candle(_candle(2, o=1999, h=2008, l=1995, c=2005), 2)
        # exit(1995) = 1995 - 0.03 -> adverse -5.03 -> -$50.30
        # exit(2008) = 2008 - 0.03 -> favourable +7.97 -> +$79.70
        assert pos.mae_usd == pytest.approx(-50.30)
        assert pos.mfe_usd == pytest.approx(79.70)

    def test_sell_exit_slippage(self):
        pos = OpenPosition(fill=_fill("SELL", sl_price=2010.0),
                           value_per_point_per_lot=1.0, point=0.01,
                           spread_points=21, exit_slippage_points=3)
        pos.check_candle(_candle(2, o=2000, h=2005, l=1995, c=2000), 2)
        # exit(2005) = 2005 + 0.21 + 0.03 = 2005.24 -> adverse -5.24 -> -$52.40
        assert pos.mae_usd == pytest.approx(-52.40)
        # exit(1995) = 1995.24 -> favourable +4.76 -> +$47.60
        assert pos.mfe_usd == pytest.approx(47.60)


class TestEntryCandleExcursions:
    def test_fill_bar_extremes_observed(self):
        pos = OpenPosition(fill=_fill("BUY"), value_per_point_per_lot=1.0,
                           point=0.01)
        status, _ = pos.check_entry_candle(_candle(2, o=2000, h=2004, l=1996, c=2003))
        assert status.value == "OPEN"   # no SL/TP touched (sl 1990, tp 2020)
        assert pos.mae_usd == pytest.approx(-40.0)
        assert pos.mfe_usd == pytest.approx(40.0)

    def test_entry_candle_immediate_exit_includes_extremes(self):
        pos = OpenPosition(fill=_fill("BUY"), value_per_point_per_lot=1.0,
                           point=0.01)
        status, _ = pos.check_entry_candle(_candle(2, o=2000, h=2003, l=1988, c=1989))
        assert status.value == "HIT_SL"
        closed = pos.close(1988.0, 2, BASE + 2 * M5, "SL")
        # bar low 1988 -> -$120; realised -120 identical
        assert closed.mae_usd == pytest.approx(-120.0)
        assert closed.mae_r == pytest.approx(-1.2)
        assert closed.mfe_usd == pytest.approx(30.0)   # bar high 2003


class TestSLTPClosures:
    def test_sl_closure_mae_matches_risk(self):
        """SL closure: MAE reflects the worst intrabar excursion (the bar low
        dips one point past the stop), realised P&L stays at the stop, and
        both sit inside [mae, mfe]."""
        pos = OpenPosition(fill=_fill("BUY", sl_price=1990.0),
                           value_per_point_per_lot=1.0, point=0.01)
        pos.check_candle(_candle(2, o=2000, h=2002, l=1995, c=1998), 2)
        pos.check_candle(_candle(3, o=1998, h=1999, l=1989, c=1990), 3)
        closed = pos.close(1990.0, 3, BASE + 3 * M5, "SL")
        assert closed.exit_reason == "SL"
        assert closed.pnl_usd == pytest.approx(-100.0)
        assert closed.mae_usd == pytest.approx(-110.0)   # bar low 1989 < stop
        assert closed.mae_r == pytest.approx(-1.1)
        assert closed.mfe_usd == pytest.approx(20.0)     # bar2 high 2002
        assert closed.mae_usd <= closed.pnl_usd <= closed.mfe_usd

    def test_tp_closure_mfe_matches_target(self):
        pos = OpenPosition(fill=_fill("BUY", sl_price=1990.0),
                           value_per_point_per_lot=1.0, point=0.01)
        pos.check_candle(_candle(2, o=2000, h=2005, l=1999, c=2004), 2)
        pos.check_candle(_candle(3, o=2005, h=2020, l=2004, c=2020), 3)
        closed = pos.close(2020.0, 3, BASE + 3 * M5, "TP")
        assert closed.pnl_usd == pytest.approx(200.0)
        assert closed.mfe_usd == pytest.approx(200.0)
        assert closed.mfe_r == pytest.approx(2.0)
        assert closed.mae_usd == pytest.approx(-10.0)   # bar2 low 1999


class TestEngineIntegration:
    def _run(self, setups, candles, cfg):
        with patch("mt5_liquidity.backtest.engine.run_liquidity_sweep_strategy",
                   return_value=setups):
            return run_backtest([], candles, cfg, XAUUSD)

    def _cfg(self, **overrides) -> BacktestConfig:
        defaults = dict(risk_pct=1.0, rr_target=2.0, max_positions=1,
                        spread_points=0, slippage_points=0,
                        max_daily_loss_pct=99.0, initial_equity=10_000.0,
                        commission_usd=0.0)
        defaults.update(overrides)
        return BacktestConfig(**defaults)

    def _setup(self, setup_id, direction, sl_price, rejection_idx=1):
        sweep = SweepSignal(
            zone_id="Z1", zone_type=ZoneType.BSL if direction == "SELL" else ZoneType.SSL,
            sweep_candle_index=0, sweep_candle_ts=BASE,
            wick_extreme=2012.0 if direction == "SELL" else 1988.0,
            close_price=2000.0, zone_upper=2012.0, zone_lower=1988.0,
            min_sweep_points=5)
        retest = RetestSignal(
            mss_zone_id="Z1", direction="BEARISH" if direction == "SELL" else "BULLISH",
            structure_level=2000.0, retest_band_upper=2001.0, retest_band_lower=1999.0,
            rejection_candle_index=rejection_idx,
            rejection_candle_ts=BASE + rejection_idx * M5,
            rejection_candle_close=2000.0, rejection_candle_open=2000.0,
            signal_price=2000.0, bars_after_mss=2)
        return Setup(setup_id=setup_id, direction=direction, sweep=sweep,
                     retest=retest, state=SetupState.ENTRY_READY,
                     signal_price=2000.0, stop_loss_price=sl_price)

    def test_tp_trade_full_excursion_path(self):
        cfg = self._cfg()
        candles = [
            _candle(0),
            _candle(1, o=2000, h=2001, l=1999, c=2000),   # rejection
            _candle(2, o=2000, h=2002, l=1999, c=2000),   # BUY fills at idx2
            _candle(3, o=2000, h=2002, l=1995, c=1999),   # adverse dip -50
            _candle(4, o=2005, h=2020, l=2004, c=2020),   # TP 2020 hit
        ]
        result = self._run([self._setup("M2_BUY", "BUY", 1990.0)], candles, cfg)
        assert result.trades_opened == 1
        trade = result.closed_positions[0]
        assert trade.exit_reason == "TP"
        assert trade.lots == pytest.approx(0.1)          # 1% risk on 1000-pt SL
        assert trade.mae_usd == pytest.approx(-50.0)
        assert trade.mfe_usd == pytest.approx(200.0)
        assert trade.mae_r == pytest.approx(-0.5)
        assert trade.mfe_r == pytest.approx(2.0)
        # Ledger aggregates see the same values.
        assert result.stats.worst_mae_usd == pytest.approx(-50.0)
        assert result.stats.best_mfe_usd == pytest.approx(200.0)
        assert result.stats.avg_mae_usd == pytest.approx(-50.0)

    def test_risk_closure_excursions(self):
        """CLOSE_ALL risk closure: bar path + realised exit both included."""
        cfg = self._cfg()
        cfg.account_risk = AccountRiskConfig(enabled=True, daily_loss_usd=50.0,
                                             open_position_action="CLOSE_ALL")
        candles = [
            _candle(0),
            _candle(1, o=2000, h=2001, l=1999, c=2000),
            _candle(2, o=2000, h=2001, l=1999, c=2000),   # SELL fills at idx2
            _candle(3, o=2000, h=2012, l=1999, c=2010),   # floating -72 → trigger
        ]
        result = self._run([self._setup("M2_RISK", "SELL", 2015.0)], candles, cfg)
        trade = result.closed_positions[0]
        assert trade.exit_reason == "RISK_DAILY_LOSS"
        lots = trade.lots                                 # 0.06 (1% risk, 1500-pt SL)
        assert trade.mae_usd == pytest.approx(-12.0 / 0.01 * 1.0 * lots)   # bar high 2012
        assert trade.mfe_usd == pytest.approx(1.0 / 0.01 * 1.0 * lots)     # bar low 1999
        # realised (-60) lies within [mae, mfe]
        assert trade.mae_usd <= trade.pnl_usd <= trade.mfe_usd

    def test_force_close_zero_excursion(self):
        cfg = self._cfg()
        candles = [_candle(i, o=2000, h=2000, l=2000, c=2000) for i in range(6)]
        result = self._run([self._setup("M2_FC", "SELL", 2015.0)], candles, cfg)
        trade = result.closed_positions[0]
        assert trade.exit_reason == "FORCE_CLOSE"
        assert trade.pnl_usd == 0.0
        assert trade.mae_usd == 0.0
        assert trade.mfe_usd == 0.0


class TestLedgerReconciliation:
    def _trade(self, mae, mfe, pnl, setup_id):
        return ClosedPosition(
            setup_id=setup_id, direction="BUY", fill_price=2000.0,
            close_price=2001.0, tp_price=2010.0, sl_price=1990.0, lots=0.1,
            pnl_usd=pnl, exit_reason="TP", fill_candle_index=1,
            close_candle_index=3, fill_ts=BASE, close_ts=BASE + 3 * M5,
            bars_held=2, value_per_point_per_lot=1.0, point=0.01,
            mae_usd=mae, mfe_usd=mfe, mae_r=mae / 100.0, mfe_r=mfe / 100.0)

    def test_aggregates(self):
        ledger = TradeLedger(initial_equity=10_000.0)
        ledger.record(self._trade(-50.0, 80.0, 20.0, "A"))
        ledger.record(self._trade(-10.0, 200.0, -5.0, "B"))
        stats = ledger.compute_stats()
        assert stats.avg_mae_usd == pytest.approx(-30.0)
        assert stats.avg_mfe_usd == pytest.approx(140.0)
        assert stats.worst_mae_usd == pytest.approx(-50.0)
        assert stats.best_mfe_usd == pytest.approx(200.0)

    def test_empty_ledger_defaults(self):
        stats = TradeLedger(initial_equity=10_000.0).compute_stats()
        assert stats.avg_mae_usd == 0.0 and stats.avg_mfe_usd == 0.0
        assert stats.worst_mae_usd == 0.0 and stats.best_mfe_usd == 0.0

    def test_group_aggregation_reconciles_with_ledger(self):
        trades = [self._trade(-50.0, 80.0, 20.0, "A"),
                  self._trade(-10.0, 200.0, -5.0, "B")]
        group = aggregate_trade_stats(trades)
        ledger = TradeLedger(initial_equity=10_000.0)
        for t in trades:
            ledger.record(t)
        ls = ledger.compute_stats()
        assert group.avg_mae_usd == pytest.approx(ls.avg_mae_usd)
        assert group.worst_mae_usd == pytest.approx(ls.worst_mae_usd)
        assert group.best_mfe_usd == pytest.approx(ls.best_mfe_usd)


class TestBackwardCompatibilityAndExport:
    def test_closed_position_constructs_without_new_fields(self):
        trade = ClosedPosition(
            setup_id="s", direction="BUY", fill_price=2000.0, close_price=2001.0,
            tp_price=2010.0, sl_price=1990.0, lots=0.1, pnl_usd=1.0,
            exit_reason="TP", fill_candle_index=1, close_candle_index=2,
            fill_ts=BASE, close_ts=BASE + M5, bars_held=1,
            value_per_point_per_lot=1.0, point=0.01)
        assert trade.mae_usd == 0.0 and trade.mfe_r == 0.0

    def test_trades_csv_includes_excursion_columns(self, tmp_path):
        from mt5_liquidity.backtest.engine import BacktestResult, MarkToMarketPoint
        from mt5_liquidity.backtest.ledger import LedgerStats
        trade = ClosedPosition(
            setup_id="s", direction="BUY", fill_price=2000.0, close_price=2001.0,
            tp_price=2010.0, sl_price=1990.0, lots=0.1, pnl_usd=10.0,
            exit_reason="TP", fill_candle_index=1, close_candle_index=2,
            fill_ts=BASE, close_ts=BASE + M5, bars_held=1,
            value_per_point_per_lot=1.0, point=0.01,
            mae_usd=-20.0, mfe_usd=10.0, mae_r=-0.2, mfe_r=0.1)
        stats = LedgerStats(
            total_trades=1, winners=1, losers=0, win_rate=1.0, gross_profit=10.0,
            gross_loss=0.0, total_commission=0.0, net_pnl=10.0, profit_factor=2.0,
            avg_win=10.0, avg_loss=0.0, max_drawdown_usd=0.0, max_drawdown_pct=0.0,
            final_equity=10_010.0)
        result = BacktestResult(
            setups_total=1, setups_entry_ready=1, setups_skipped_max_pos=0,
            setups_skipped_daily_loss=0, setups_skipped_sizing=0,
            setups_skipped_no_next_bar=0, trades_opened=1,
            closed_positions=[trade], force_closed=0, stats=stats,
            equity_curve=[(BASE + M5, 10_010.0)], rejected_sizings=[],
            mark_to_market_curve=[MarkToMarketPoint(BASE, 10_000.0, 0.0, 10_000.0, 0)])
        report_dir = export_backtest_report(
            result=result, symbol="XAUUSD", from_date="2026-11-09",
            to_date="2026-11-10", config=BacktestConfig(),
            symbol_meta=XAUUSD, reports_dir=tmp_path,
            run_timestamp=datetime(2026, 11, 10, 18, 30, tzinfo=UTC))
        with (report_dir / "trades.csv").open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            assert {"mae_usd", "mfe_usd", "mae_r", "mfe_r"} <= set(reader.fieldnames)
            row = next(iter(reader))
            assert float(row["mae_usd"]) == -20.0
            assert float(row["mfe_r"]) == 0.1
        summary = json.loads((report_dir / "analytics" / "analytics_summary.json")
                             .read_text(encoding="utf-8"))
        assert summary["duration_summary"]["mae_mfe"].startswith("available")
        with (report_dir / "analytics" / "duration_analysis.csv").open(
                newline="", encoding="utf-8") as fh:
            assert {"avg_mae_usd", "avg_mfe_usd", "worst_mae_usd",
                    "best_mfe_usd"} <= set(csv.DictReader(fh).fieldnames)
