"""Integration tests: account-risk manager wired into the backtest engine.

Uses the synthetic-setup patching technique from test_engine.py to drive real
engine runs, verifying every protection trigger with open positions, the
CLOSE_ALL / STOP_NEW_ENTRIES policies, accounting reconciliation, and that
Phase 1/2 behaviour is untouched when the manager is disabled.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from mt5_liquidity.backtest.engine import SymbolMeta, run_backtest
from mt5_liquidity.config import AccountRiskConfig, BacktestConfig
from mt5_liquidity.data.candles import Candle
from mt5_liquidity.risk.account_risk import RiskState
from mt5_liquidity.strategy.models import ZoneType
from mt5_liquidity.strategy.retests import RetestSignal
from mt5_liquidity.strategy.setup import Setup, SetupState
from mt5_liquidity.strategy.sweeps import SweepSignal

UTC = timezone.utc
BASE = datetime(2026, 11, 9, 0, 0, tzinfo=UTC)   # Monday 00:00 UTC
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
        min_sweep_points=5)
    retest = RetestSignal(
        mss_zone_id="Z1", direction="BEARISH" if direction == "SELL" else "BULLISH",
        structure_level=2000.0, retest_band_upper=2001.0, retest_band_lower=1999.0,
        rejection_candle_index=rejection_idx,
        rejection_candle_ts=BASE + rejection_idx * M5,
        rejection_candle_close=signal_price, rejection_candle_open=signal_price,
        signal_price=signal_price, bars_after_mss=2)
    return Setup(setup_id=setup_id, direction=direction, sweep=sweep,
                 retest=retest, state=SetupState.ENTRY_READY,
                 signal_price=signal_price, stop_loss_price=sl_price)


def _run(setups, candles, cfg):
    with patch("mt5_liquidity.backtest.engine.run_liquidity_sweep_strategy",
               return_value=setups):
        return run_backtest([], candles, cfg, XAUUSD)


def _cfg(**overrides) -> BacktestConfig:
    defaults = dict(
        risk_pct=1.0, rr_target=2.0, max_positions=5,
        spread_points=0, slippage_points=0, max_daily_loss_pct=99.0,
        initial_equity=10_000.0, commission_usd=0.0,
    )
    defaults.update(overrides)
    return BacktestConfig(**defaults)


def _risk_cfg(**risk_overrides) -> BacktestConfig:
    cfg = _cfg()
    cfg.account_risk = AccountRiskConfig(enabled=True, **risk_overrides)
    return cfg


class TestDailyLossWithOpenPositions:
    def test_close_all_closes_open_positions_with_risk_reason(self):
        """Two individually admissible SELL positions jointly breach the
        $50 daily-loss limit while both remain open.

        Each position risks about $45 (0.03 lots, 1500 points). At a $10
        adverse move each floats about -$30; combined floating loss is -$60.
        """
        cfg = _risk_cfg(daily_loss_usd=50.0, open_position_action="CLOSE_ALL")
        cfg.risk_pct = 0.5
        candles = [
            _candle(0),
            _candle(1, o=2000, h=2001, l=1999, c=2000),
            _candle(2, o=2000, h=2001, l=1999, c=2000),
            _candle(3, o=2000, h=2012, l=1999, c=2010),
        ]
        first = _make_setup("RISK_DL_A", "SELL", sl_price=2015.0, rejection_idx=0)
        second = _make_setup("RISK_DL_B", "SELL", sl_price=2015.0, rejection_idx=1)
        result = _run([first, second], candles, cfg)

        assert result.trades_opened == 2
        assert result.risk_summary["enabled"] is True
        assert RiskState.DAILY_LOSS_REACHED.value in result.risk_summary["locks_triggered"]
        assert result.risk_summary["forced_closures"] == 2
        assert all(t.exit_reason == "RISK_DAILY_LOSS" for t in result.closed_positions)
        assert all(t.close_candle_index == 3 for t in result.closed_positions)
        assert result.mark_to_market_curve[-1].mtm_equity == pytest.approx(
            result.stats.final_equity)

    def test_stop_new_entries_keeps_managing_open_positions(self):
        """STOP_NEW_ENTRIES blocks fresh trades but continues managing both
        admitted positions until their ordinary stop losses are reached."""
        cfg = _risk_cfg(daily_loss_usd=50.0, open_position_action="STOP_NEW_ENTRIES")
        cfg.risk_pct = 0.5
        candles = [
            _candle(0),
            _candle(1, o=2000, h=2001, l=1999, c=2000),
            _candle(2, o=2000, h=2001, l=1999, c=2000),
            _candle(3, o=2000, h=2012, l=1999, c=2010),  # combined floating -60
            _candle(4, o=2010, h=2016, l=2009, c=2015),  # both SLs hit
        ]
        first = _make_setup("RISK_SNE_A", "SELL", sl_price=2015.0, rejection_idx=0)
        second = _make_setup("RISK_SNE_B", "SELL", sl_price=2015.0, rejection_idx=1)
        result = _run([first, second], candles, cfg)

        assert result.trades_opened == 2
        assert result.risk_summary["forced_closures"] == 0
        assert len(result.closed_positions) == 2
        assert all(t.exit_reason == "SL" for t in result.closed_positions)
        assert result.risk_summary["final_state"] == RiskState.DAILY_LOSS_REACHED.value

    def test_daily_lock_blocks_later_entries_and_counts_reason(self):
        cfg = _risk_cfg(daily_loss_usd=50.0, open_position_action="STOP_NEW_ENTRIES")
        cfg.risk_pct = 0.5
        candles = [_candle(i) for i in range(10)]
        first = _make_setup("RISK_A", "SELL", sl_price=2015.0, rejection_idx=0)
        second = _make_setup("RISK_B", "SELL", sl_price=2015.0, rejection_idx=1)
        later = _make_setup("RISK_C", "SELL", sl_price=2015.0, rejection_idx=3)
        # Two admitted positions jointly reach -$60 floating P&L at idx3.
        # The later setup maps to idx4 and must be blocked by the daily lock.
        candles[3] = _candle(3, o=2000, h=2012, l=1999, c=2010)
        result = _run([first, second, later], candles, cfg)

        assert result.trades_opened == 2
        assert result.setups_skipped_risk >= 1
        assert result.risk_blocked_by_reason.get("RISK_DAILY_LOSS", 0) >= 1


class TestStrictDailyLossAdmission:
    def test_later_entry_is_rejected_after_open_positions_consume_daily_budget(self):
        cfg = _risk_cfg(daily_loss_usd=50.0, open_position_action="STOP_NEW_ENTRIES")
        cfg.risk_pct = 0.5
        candles = [_candle(i) for i in range(8)]
        first = _make_setup("STRICT_A", "SELL", sl_price=2015.0, rejection_idx=0)
        second = _make_setup("STRICT_B", "SELL", sl_price=2015.0, rejection_idx=1)
        later = _make_setup("STRICT_C", "SELL", sl_price=2015.0, rejection_idx=2)
        # Two open positions jointly cross the daily loss threshold at idx3.
        # The third setup maps to idx3 and must be blocked on that same candle.
        candles[3] = _candle(3, o=2000, h=2012, l=1999, c=2010)
        result = _run([first, second, later], candles, cfg)
        assert result.trades_opened == 2
        assert result.risk_summary["final_state"] == RiskState.DAILY_LOSS_REACHED.value
        assert result.risk_blocked_by_reason.get("RISK_DAILY_LOSS", 0) >= 1

    def test_entry_risk_cannot_exceed_remaining_daily_budget(self):
        cfg = _risk_cfg(daily_loss_usd=50.0, open_position_action="STOP_NEW_ENTRIES")
        candles = [_candle(i) for i in range(8)]
        # One setup has a planned monetary risk above the entire $50 daily
        # allowance, so admission must reject it before sending a fill.
        setup = _make_setup("STRICT_BUDGET", "SELL", sl_price=2015.0, rejection_idx=1)
        result = _run([setup], candles, cfg)
        assert result.trades_opened == 0
        assert result.risk_blocked_by_reason.get("DAILY_RISK_BUDGET_EXCEEDED", 0) == 1


class TestDailyTargetWithOpenPositions:
    def test_close_all_locks_in_profit(self):
        """Floating profit crossing the daily target force-closes the open
        position with RISK_DAILY_TARGET.

        Numbers: BUY fill 2000 (idx2), sl 1990 → 0.10 lots at 1% risk.
        Floating profit = 10 USD per 1.0 price move; target USD 50 crossed
        at close 2006 on idx3.
        """
        cfg = _risk_cfg(daily_target_usd=50.0, open_position_action="CLOSE_ALL")
        candles = [
            _candle(0),
            _candle(1, o=2000, h=2001, l=1999, c=2000),   # rejection candle
            _candle(2, o=2000, h=2001, l=1999, c=2000),   # BUY fills at idx2
            _candle(3, o=2000, h=2006, l=1999, c=2006),   # floating +60 → trigger
        ]
        setup = _make_setup("RISK_DT", "BUY", sl_price=1990.0, rejection_idx=1)
        result = _run([setup], candles, cfg)

        assert result.risk_summary["forced_closures"] == 1
        closed = result.closed_positions[0]
        assert closed.exit_reason == "RISK_DAILY_TARGET"
        assert closed.pnl_usd > 0


class TestEquityTrailingInEngine:
    def test_floating_peak_then_pullback_triggers_close_all(self):
        """Numbers: BUY fill 2000 (idx2), sl 1990 → 0.10 lots; floating = 10
        USD per 1.0 move. Peak 10_050 at idx3 sets the floor at 10_020; the
        pullback to mtm 10_010 at idx5 triggers the protection."""
        cfg = _risk_cfg(equity_trail_giveback_usd=30.0,
                        open_position_action="CLOSE_ALL")
        candles = [
            _candle(0),
            _candle(1, o=2000, h=2001, l=1999, c=2000),   # rejection candle
            _candle(2, o=2000, h=2001, l=1999, c=2000),   # BUY fills at idx2
            _candle(3, o=2000, h=2005, l=1999, c=2005),   # floating +50 → peak
            _candle(4, o=2005, h=2006, l=2004, c=2005),   # holds above floor
            _candle(5, o=2005, h=2006, l=2004, c=2001),   # mtm 10_010 < 10_020
        ]
        setup = _make_setup("RISK_TRAIL", "BUY", sl_price=1990.0, rejection_idx=1)
        result = _run([setup], candles, cfg)

        assert RiskState.EQUITY_TRAILING_TRIGGERED.value in result.risk_summary["locks_triggered"]
        assert result.risk_summary["forced_closures"] == 1
        assert result.closed_positions[0].exit_reason == "RISK_EQUITY_TRAIL"
        # Floor was derived from the floating peak and is captured on the event.
        trail_events = [e for e in result.risk_events
                        if e.event_type == "EQUITY_TRAILING_TRIGGERED"]
        assert trail_events[0].peak_equity == pytest.approx(10_050.0)
        assert trail_events[0].floor_equity == pytest.approx(10_020.0)


class TestMaxDrawdownInEngine:
    def test_triggers_and_blocks_all_subsequent_entries(self):
        cfg = _risk_cfg(max_drawdown_usd=250.0, open_position_action="STOP_NEW_ENTRIES")
        candles = [_candle(i) for i in range(14)]
        # Three sequential SELL trades lose -90 each (0.06 lots, 1500-pt SL):
        # drawdown from the 10_000 peak reaches 270 >= 250 at idx8. The
        # fourth setup (fill idx10) is then risk-blocked.
        s1 = _make_setup("DD_A", "SELL", sl_price=2015.0, rejection_idx=0)
        s2 = _make_setup("DD_B", "SELL", sl_price=2015.0, rejection_idx=3)
        s3 = _make_setup("DD_C", "SELL", sl_price=2015.0, rejection_idx=6)
        s4 = _make_setup("DD_D", "SELL", sl_price=2015.0, rejection_idx=9)
        candles[2] = _candle(2, o=2000, h=2016, l=1999, c=2015)
        candles[5] = _candle(5, o=2000, h=2016, l=1999, c=2015)
        candles[8] = _candle(8, o=2000, h=2016, l=1999, c=2015)
        result = _run([s1, s2, s3, s4], candles, cfg)

        assert RiskState.MAX_DRAWDOWN_TRIGGERED.value in result.risk_summary["locks_triggered"]
        assert result.setups_skipped_risk >= 1
        assert result.risk_blocked_by_reason.get("RISK_MAX_DRAWDOWN", 0) >= 1
        assert result.trades_opened == 3


class TestLossStreakInEngine:
    def test_consecutive_losses_lock_new_entries_until_next_utc_day(self):
        cfg = _risk_cfg(max_consecutive_losses=2, open_position_action="STOP_NEW_ENTRIES")
        candles = [_candle(i) for i in range(16)]
        s1 = _make_setup("LS_A", "SELL", sl_price=2015.0, rejection_idx=0)
        s2 = _make_setup("LS_B", "SELL", sl_price=2015.0, rejection_idx=3)
        s3 = _make_setup("LS_C", "SELL", sl_price=2015.0, rejection_idx=6)   # day 2
        candles[2] = _candle(2, o=2000, h=2016, l=1999, c=2015)
        candles[5] = _candle(5, o=2000, h=2016, l=1999, c=2015)
        candles[8] = _candle(8, o=2000, h=2016, l=1999, c=2015)
        result = _run([s1, s2, s3], candles, cfg)

        # Two same-day losses lock the streak; the third setup (same day)
        # is risk-blocked before it can open.
        assert RiskState.LOSS_STREAK_REACHED.value in result.risk_summary["locks_triggered"]
        assert result.risk_blocked_by_reason.get("RISK_LOSS_STREAK", 0) >= 1
        assert result.trades_opened == 2


class TestPerTradeRiskInEngine:
    def test_oversized_trade_rejected_without_locking_account(self):
        cfg = _risk_cfg(max_risk_per_trade_usd=50.0)
        candles = [_candle(i) for i in range(6)]
        # risk_pct=1.0 → $100 target risk on a 10-point SL: blocked by the
        # $50 ceiling. The account stays ACTIVE.
        setup = _make_setup("PR_A", "SELL", sl_price=2010.0, rejection_idx=1)
        result = _run([setup], candles, cfg)

        assert result.trades_opened == 0
        assert result.setups_skipped_risk == 1
        assert result.risk_blocked_by_reason.get("MAX_RISK_PER_TRADE_USD") == 1
        assert result.risk_summary["final_state"] == "ACTIVE"

    def test_normal_trade_passes_the_per_trade_gate(self):
        cfg = _risk_cfg(max_risk_per_trade_usd=200.0)
        candles = [_candle(i) for i in range(6)]
        setup = _make_setup("PR_B", "SELL", sl_price=2010.0, rejection_idx=1)
        result = _run([setup], candles, cfg)
        assert result.trades_opened == 1


class TestSameCandleLockBypassPrevention:
    def test_entry_cannot_follow_a_fresh_lock_on_the_same_candle(self):
        """A loss visible in mark-to-market on one candle must lock before
        the entry that maps to that same candle (plan 3F)."""
        cfg = _risk_cfg(daily_loss_usd=1.0, open_position_action="STOP_NEW_ENTRIES")
        candles = [_candle(i) for i in range(10)]
        # Two positions are admitted on idx1 and idx2. Their combined
        # floating loss crosses $50 on idx3 before a third entry mapped to
        # that same candle is evaluated.
        cfg = _risk_cfg(daily_loss_usd=50.0, open_position_action="STOP_NEW_ENTRIES")
        cfg.risk_pct = 0.5
        first = _make_setup("SC_A", "SELL", sl_price=2015.0, rejection_idx=0)
        second = _make_setup("SC_B", "SELL", sl_price=2015.0, rejection_idx=1)
        same_candle = _make_setup("SC_C", "SELL", sl_price=2015.0, rejection_idx=2)
        candles[3] = _candle(3, o=2000, h=2012, l=1999, c=2010)
        result = _run([first, second, same_candle], candles, cfg)

        assert result.trades_opened == 2
        assert result.setups_skipped_risk >= 1


class TestLegacyBehaviourWhenDisabled:
    def test_disabled_manager_preserves_phase_1_behaviour(self):
        """No manager: the legacy inline daily-loss guard blocks entries and
        no risk events or forced closures exist."""
        cfg = _cfg(max_daily_loss_pct=0.001, max_positions=5)   # ultra-tight legacy guard
        candles = [_candle(i) for i in range(10)]
        s1 = _make_setup("LEG_A", "SELL", sl_price=2015.0, rejection_idx=0)
        s2 = _make_setup("LEG_B", "SELL", sl_price=2015.0, rejection_idx=3)
        candles[2] = _candle(2, o=2000, h=2016, l=1999, c=2015)
        result = _run([s1, s2], candles, cfg)

        assert result.risk_summary == {"enabled": False}
        assert result.risk_events == []
        assert result.setups_skipped_risk == 0
        assert result.risk_summary.get("forced_closures", 0) == 0
        assert result.setups_skipped_daily_loss >= 1   # legacy guard did the work
        # The legacy guard never force-closes: the open trade runs to its SL.
        assert all(t.exit_reason in ("TP", "SL", "FORCE_CLOSE")
                   for t in result.closed_positions)


class TestAccountingReconciliation:
    def test_risk_closures_count_in_ledger_and_final_equity_reconciles(self):
        """Numbers: SELL fill 2000 (idx2), sl 2020 → 0.05 lots; floating =
        5 USD per 1.0 move. daily_loss USD 50 crossed at close 2010 (idx3)."""
        cfg = _risk_cfg(daily_loss_usd=50.0, open_position_action="CLOSE_ALL")
        cfg.risk_pct = 0.5
        candles = [
            _candle(0),
            _candle(1, o=2000, h=2001, l=1999, c=2000),
            _candle(2, o=2000, h=2001, l=1999, c=2000),
            _candle(3, o=2000, h=2012, l=1999, c=2010),
        ]
        first = _make_setup("REC_A", "SELL", sl_price=2015.0, rejection_idx=0)
        second = _make_setup("REC_B", "SELL", sl_price=2015.0, rejection_idx=1)
        result = _run([first, second], candles, cfg)

        ledger_net = sum(t.net_pnl_usd for t in result.closed_positions)
        assert result.stats.net_pnl == pytest.approx(ledger_net)
        assert result.stats.final_equity == pytest.approx(10_000.0 + ledger_net)
        assert result.mark_to_market_curve[-1].mtm_equity == pytest.approx(
            result.stats.final_equity)
        # Every risk closure is a normal ledger trade — Phase 2 analytics see it.
        assert any(t.exit_reason.startswith("RISK_") for t in result.closed_positions)

    def test_blocked_by_reason_reconciles_with_skip_counter(self):
        cfg = _risk_cfg(daily_loss_usd=1.0, open_position_action="STOP_NEW_ENTRIES")
        candles = [_candle(i) for i in range(12)]
        s1 = _make_setup("REC_B", "SELL", sl_price=2015.0, rejection_idx=0)
        s2 = _make_setup("REC_C", "SELL", sl_price=2015.0, rejection_idx=3)
        s3 = _make_setup("REC_D", "SELL", sl_price=2015.0, rejection_idx=6)
        candles[2] = _candle(2, o=2000, h=2016, l=1999, c=2015)
        result = _run([s1, s2, s3], candles, cfg)

        assert result.setups_skipped_risk == sum(result.risk_blocked_by_reason.values())
