"""Unit tests for the Phase 3 account-level risk manager.

Covers every protection rule, boundary conditions, UTC daily resets,
multiple simultaneous triggers, open-position policy signals, per-trade risk
ceilings, manual halt, and state persistence (plan 3B-3G).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mt5_liquidity.risk.account_risk import (
    AccountRiskManager,
    RiskState,
    RiskStateError,
    build_account_risk_manager,
    config_fingerprint,
)
from mt5_liquidity.config import AccountRiskConfig, BacktestConfig, validate_account_risk_config

UTC = timezone.utc
MONDAY = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)  # normal UTC Monday


def _manager(**overrides) -> AccountRiskManager:
    defaults = dict(initial_equity=10_000.0)
    defaults.update(overrides)
    return AccountRiskManager(**defaults)


def _candle_obs(mgr: AccountRiskManager, ts: datetime, realised: float,
                floating: float = 0.0, open_positions: int = 0):
    return mgr.on_candle(ts, realised_equity=realised, floating_pnl=floating,
                         open_positions=open_positions)


class TestConfigurationValidation:
    def test_valid_config_passes(self):
        cfg = AccountRiskConfig(enabled=True, daily_loss_pct=2.0, max_drawdown_pct=10.0)
        assert validate_account_risk_config(cfg) is None

    def test_default_config_is_disabled_and_valid(self):
        cfg = AccountRiskConfig()
        assert cfg.enabled is False
        assert validate_account_risk_config(cfg) is None

    def test_negative_threshold_rejected(self):
        assert "positive" in validate_account_risk_config(
            AccountRiskConfig(daily_loss_usd=-5.0))

    def test_zero_threshold_rejected(self):
        assert "positive" in validate_account_risk_config(
            AccountRiskConfig(max_drawdown_usd=0.0))

    def test_pct_over_100_rejected(self):
        assert "<= 100" in validate_account_risk_config(
            AccountRiskConfig(daily_target_pct=150.0))

    def test_unknown_timezone_rejected(self):
        assert "daily_reset_timezone" in validate_account_risk_config(
            AccountRiskConfig(daily_reset_timezone="Mars/Olympus"))

    def test_bad_open_position_action_rejected(self):
        assert "open_position_action" in validate_account_risk_config(
            AccountRiskConfig(open_position_action="WATCH"))

    def test_streak_must_be_positive(self):
        assert "max_consecutive_losses" in validate_account_risk_config(
            AccountRiskConfig(max_consecutive_losses=0))

    def test_disabled_manager_returns_none(self):
        cfg = BacktestConfig()
        assert build_account_risk_manager(cfg) is None

    def test_invalid_enabled_config_raises_from_factory(self):
        cfg = BacktestConfig()
        cfg.account_risk = AccountRiskConfig(enabled=True, daily_loss_usd=-1.0)
        with pytest.raises(ValueError, match="Invalid account risk configuration"):
            build_account_risk_manager(cfg)


class TestDailyLossProtection:
    def test_triggers_on_realised_loss(self):
        mgr = _manager(daily_loss_usd=100.0)
        _candle_obs(mgr, MONDAY, 10_000.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=5), 9_899.0)
        assert mgr.state is RiskState.DAILY_LOSS_REACHED

    def test_boundary_at_exact_threshold_triggers(self):
        mgr = _manager(daily_loss_usd=100.0)
        _candle_obs(mgr, MONDAY, 10_000.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=5), 9_900.0)  # exactly -100
        assert mgr.state is RiskState.DAILY_LOSS_REACHED

    def test_just_below_threshold_stays_active(self):
        mgr = _manager(daily_loss_usd=100.0)
        _candle_obs(mgr, MONDAY, 10_000.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=5), 9_900.01)
        assert mgr.state is RiskState.ACTIVE

    def test_floating_loss_triggers_without_realised(self):
        mgr = _manager(daily_loss_usd=100.0)
        _candle_obs(mgr, MONDAY, 10_000.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=5), 10_000.0,
                    floating=-120.0, open_positions=1)
        assert mgr.state is RiskState.DAILY_LOSS_REACHED

    def test_pct_form_uses_daily_baseline(self):
        mgr = _manager(daily_loss_pct=2.0)
        _candle_obs(mgr, MONDAY, 10_000.0)             # baseline 10_000
        _candle_obs(mgr, MONDAY + timedelta(minutes=5), 9_799.0)  # -2.01%
        assert mgr.state is RiskState.DAILY_LOSS_REACHED

    def test_usd_and_pct_take_the_tighter_threshold(self):
        # USD 100 vs 2% of 10_000 (= 200): the tighter USD 100 triggers first.
        mgr = _manager(daily_loss_usd=100.0, daily_loss_pct=2.0)
        _candle_obs(mgr, MONDAY, 10_000.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=5), 9_899.0)  # -101
        assert mgr.state is RiskState.DAILY_LOSS_REACHED
        event = next(e for e in mgr.events if e.event_type == "DAILY_LOSS_TRIGGERED")
        assert event.threshold_value == pytest.approx(100.0)

    def test_loss_lock_blocks_entry_and_resets_next_utc_day(self):
        mgr = _manager(daily_loss_usd=100.0)
        _candle_obs(mgr, MONDAY, 10_000.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=5), 9_899.0)
        decision = mgr.evaluate_entry(MONDAY + timedelta(minutes=10),
                                      realised_equity=9_899.0, floating_pnl=0.0)
        assert decision.allowed is False
        assert decision.reason_code == "RISK_DAILY_LOSS"

        next_day = datetime(2026, 11, 10, 0, 5, tzinfo=UTC)
        _candle_obs(mgr, next_day, 9_899.0)
        assert mgr.state is RiskState.ACTIVE
        assert mgr.evaluate_entry(next_day, realised_equity=9_899.0,
                                  floating_pnl=0.0).allowed is True


class TestDailyTargetProtection:
    def test_triggers_on_realised_profit_and_locks(self):
        mgr = _manager(daily_target_usd=100.0)
        _candle_obs(mgr, MONDAY, 10_000.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=5), 10_100.0)
        assert mgr.state is RiskState.DAILY_TARGET_REACHED
        assert mgr.evaluate_entry(MONDAY + timedelta(minutes=10),
                                  realised_equity=10_100.0,
                                  floating_pnl=0.0).allowed is False

    def test_boundary_exact_target_triggers(self):
        mgr = _manager(daily_target_usd=100.0)
        _candle_obs(mgr, MONDAY, 10_000.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=5), 10_100.0)
        assert mgr.state is RiskState.DAILY_TARGET_REACHED

    def test_floating_profit_triggers(self):
        mgr = _manager(daily_target_usd=100.0)
        _candle_obs(mgr, MONDAY, 10_000.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=5), 10_000.0,
                    floating=150.0, open_positions=1)
        assert mgr.state is RiskState.DAILY_TARGET_REACHED

    def test_resets_next_utc_day(self):
        mgr = _manager(daily_target_usd=100.0)
        _candle_obs(mgr, MONDAY, 10_000.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=5), 10_150.0)
        next_day = datetime(2026, 11, 10, 0, 5, tzinfo=UTC)
        _candle_obs(mgr, next_day, 10_150.0)
        assert mgr.state is RiskState.ACTIVE
        # baseline re-anchored to the new day's opening MTM equity
        assert mgr.summary()["daily_baseline"] == pytest.approx(10_150.0)

    def test_reset_timezone_boundary_is_configurable(self):
        mgr = _manager(daily_target_usd=100.0, daily_reset_timezone="Asia/Tokyo")
        # 2026-11-09 20:00 UTC == 2026-11-10 05:00 JST
        _candle_obs(mgr, MONDAY.replace(hour=20), 10_000.0)
        _candle_obs(mgr, MONDAY.replace(hour=20, minute=5), 10_150.0)
        # 2026-11-09 21:00 UTC is still 2026-11-10 in JST — lock holds.
        _candle_obs(mgr, MONDAY.replace(hour=21), 10_150.0)
        assert mgr.state is RiskState.DAILY_TARGET_REACHED
        # 2026-11-09 15:05 UTC == 2026-11-10 00:05 JST?? No: 15:05 UTC = 00:05 JST next day
        # Use 2026-11-10 15:05 UTC = 2026-11-11 00:05 JST — a new JST day.
        _candle_obs(mgr, datetime(2026, 11, 10, 15, 5, tzinfo=UTC), 10_150.0)
        assert mgr.state is RiskState.ACTIVE


class TestDailyRiskBudget:
    def test_remaining_budget_accounts_for_realised_and_floating_loss(self):
        mgr = _manager(daily_loss_usd=50.0)
        _candle_obs(mgr, MONDAY, 10_000.0)
        assert mgr.daily_loss_remaining(realised_equity=10_000.0, floating_pnl=0.0) == pytest.approx(50.0)
        assert mgr.daily_loss_remaining(realised_equity=9_970.0, floating_pnl=-5.0) == pytest.approx(15.0)

    def test_proposed_risk_must_fit_remaining_budget(self):
        mgr = _manager(daily_loss_usd=50.0)
        _candle_obs(mgr, MONDAY, 10_000.0)
        blocked = mgr.check_daily_risk_budget(
            ts=MONDAY, proposed_risk_usd=60.0,
            realised_equity=10_000.0, floating_pnl=0.0)
        assert blocked.allowed is False
        assert blocked.reason_code == "DAILY_RISK_BUDGET_EXCEEDED"
        assert blocked.threshold_value == pytest.approx(50.0)

    def test_proposal_equal_to_remaining_budget_is_allowed(self):
        mgr = _manager(daily_loss_usd=50.0)
        _candle_obs(mgr, MONDAY, 10_000.0)
        accepted = mgr.check_daily_risk_budget(
            ts=MONDAY, proposed_risk_usd=50.0,
            realised_equity=10_000.0, floating_pnl=0.0)
        assert accepted.allowed is True


class TestEquityTrailingProtection:
    def test_floor_appears_after_peak_and_is_monotonic(self):
        mgr = _manager(equity_trail_giveback_usd=200.0)
        _candle_obs(mgr, MONDAY, 10_000.0)
        assert mgr.floor_equity == pytest.approx(9_800.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=5), 10_500.0)
        assert mgr.peak_equity == pytest.approx(10_500.0)
        assert mgr.floor_equity == pytest.approx(10_300.0)
        # Equity dips but stays above the floor — floor must never fall.
        _candle_obs(mgr, MONDAY + timedelta(minutes=10), 10_350.0)
        assert mgr.floor_equity == pytest.approx(10_300.0)
        assert mgr.state is RiskState.ACTIVE

    def test_triggers_at_or_below_floor(self):
        mgr = _manager(equity_trail_giveback_usd=200.0)
        _candle_obs(mgr, MONDAY, 10_000.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=5), 10_500.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=10), 10_300.0)  # == floor
        assert mgr.state is RiskState.EQUITY_TRAILING_TRIGGERED

    def test_just_above_floor_stays_active(self):
        mgr = _manager(equity_trail_giveback_usd=200.0)
        _candle_obs(mgr, MONDAY, 10_000.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=5), 10_500.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=10), 10_300.01)
        assert mgr.state is RiskState.ACTIVE

    def test_floating_profit_raises_peak_then_pullback_triggers(self):
        """A floating peak sets the high-water mark; the realised pullback
        below the floor triggers even with no closed profit."""
        mgr = _manager(equity_trail_giveback_usd=150.0)
        _candle_obs(mgr, MONDAY, 10_000.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=5), 10_000.0,
                    floating=300.0, open_positions=1)          # peak 10_300
        assert mgr.peak_equity == pytest.approx(10_300.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=10), 10_000.0,
                    floating=100.0, open_positions=1)          # mtm 10_100 < floor 10_150
        assert mgr.state is RiskState.EQUITY_TRAILING_TRIGGERED

    def test_lock_survives_day_rollover(self):
        mgr = _manager(equity_trail_giveback_usd=200.0)
        _candle_obs(mgr, MONDAY, 10_000.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=5), 10_500.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=10), 10_300.0)
        _candle_obs(mgr, datetime(2026, 11, 10, 0, 5, tzinfo=UTC), 10_300.0)
        assert mgr.state is RiskState.EQUITY_TRAILING_TRIGGERED  # not reset

    def test_no_new_trigger_events_while_locked(self):
        mgr = _manager(equity_trail_giveback_usd=200.0)
        _candle_obs(mgr, MONDAY, 10_000.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=5), 10_500.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=10), 10_300.0)
        triggered = [e for e in mgr.events if e.event_type == "EQUITY_TRAILING_TRIGGERED"]
        _candle_obs(mgr, MONDAY + timedelta(minutes=15), 10_100.0)
        assert [e for e in mgr.events if e.event_type == "EQUITY_TRAILING_TRIGGERED"] == triggered


class TestMaxDrawdownProtection:
    def test_usd_form_triggers_from_high_water_mark(self):
        mgr = _manager(max_drawdown_usd=500.0)
        _candle_obs(mgr, MONDAY, 10_000.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=5), 10_200.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=10), 9_700.0)  # 500 from peak
        assert mgr.state is RiskState.MAX_DRAWDOWN_TRIGGERED

    def test_pct_form(self):
        mgr = _manager(max_drawdown_pct=5.0)
        _candle_obs(mgr, MONDAY, 10_000.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=5), 9_499.0)   # 5.01% from peak
        assert mgr.state is RiskState.MAX_DRAWDOWN_TRIGGERED

    def test_takes_precedence_over_trailing_when_both_triggered(self):
        mgr = _manager(max_drawdown_usd=500.0, equity_trail_giveback_usd=100.0)
        _candle_obs(mgr, MONDAY, 10_000.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=5), 10_200.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=10), 9_690.0)  # both crossed
        assert mgr.state is RiskState.MAX_DRAWDOWN_TRIGGERED
        types = [e.event_type for e in mgr.events]
        assert "MAX_DRAWDOWN_TRIGGERED" in types
        # Trailing evaluation suppressed once max drawdown triggered (no
        # contradictory duplicate protection events).
        assert "EQUITY_TRAILING_TRIGGERED" not in types

    def test_lock_never_resets_automatically(self):
        mgr = _manager(max_drawdown_usd=500.0)
        _candle_obs(mgr, MONDAY, 10_000.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=5), 9_499.0)
        for day in range(1, 4):
            _candle_obs(mgr, datetime(2026, 11, 9 + day, 10, 0, tzinfo=UTC), 10_500.0)
        assert mgr.state is RiskState.MAX_DRAWDOWN_TRIGGERED


class TestLossStreakProtection:
    def test_streak_locks_at_limit(self):
        mgr = _manager(max_consecutive_losses=3)
        ts = MONDAY
        for i in range(3):
            mgr.on_trade_closed(ts, -10.0 - i, realised_equity=10_000.0)
            ts += timedelta(minutes=5)
        assert mgr.state is RiskState.LOSS_STREAK_REACHED
        assert mgr.loss_streak == 3

    def test_breakeven_breaks_streak(self):
        mgr = _manager(max_consecutive_losses=2)
        mgr.on_trade_closed(MONDAY, -10.0, realised_equity=10_000.0)
        mgr.on_trade_closed(MONDAY + timedelta(minutes=5), 0.0, realised_equity=10_000.0)
        mgr.on_trade_closed(MONDAY + timedelta(minutes=10), -10.0, realised_equity=10_000.0)
        assert mgr.state is RiskState.ACTIVE
        assert mgr.loss_streak == 1

    def test_win_resets_counter(self):
        mgr = _manager(max_consecutive_losses=2)
        mgr.on_trade_closed(MONDAY, -10.0, realised_equity=10_000.0)
        mgr.on_trade_closed(MONDAY + timedelta(minutes=5), 20.0, realised_equity=10_000.0)
        assert mgr.loss_streak == 0

    def test_lock_resets_next_utc_day_but_counter_persists(self):
        mgr = _manager(max_consecutive_losses=2)
        mgr.on_trade_closed(MONDAY, -10.0, realised_equity=10_000.0)
        mgr.on_trade_closed(MONDAY + timedelta(minutes=5), -10.0, realised_equity=10_000.0)
        assert mgr.state is RiskState.LOSS_STREAK_REACHED
        _candle_obs(mgr, datetime(2026, 11, 10, 0, 5, tzinfo=UTC), 9_980.0)
        assert mgr.state is RiskState.ACTIVE
        assert mgr.loss_streak == 2   # counter survives; next loss re-locks
        mgr.on_trade_closed(datetime(2026, 11, 10, 0, 10, tzinfo=UTC),
                            -10.0, realised_equity=9_970.0)
        assert mgr.state is RiskState.LOSS_STREAK_REACHED


class TestManualHalt:
    def test_halt_blocks_and_reset_releases(self):
        mgr = _manager()
        event = mgr.halt(MONDAY, realised_equity=10_000.0, reason="operator")
        assert event.event_type == "MANUAL_HALT"
        assert mgr.state is RiskState.MANUAL_HALT
        assert mgr.evaluate_entry(MONDAY, realised_equity=10_000.0,
                                  floating_pnl=0.0).allowed is False
        mgr.reset_manual_lock(MONDAY + timedelta(minutes=5), realised_equity=10_000.0)
        assert mgr.state is RiskState.ACTIVE
        assert mgr.evaluate_entry(MONDAY + timedelta(minutes=6),
                                  realised_equity=10_000.0,
                                  floating_pnl=0.0).allowed is True

    def test_halt_survives_day_rollover(self):
        mgr = _manager()
        mgr.halt(MONDAY, realised_equity=10_000.0)
        _candle_obs(mgr, datetime(2026, 11, 10, 0, 5, tzinfo=UTC), 10_000.0)
        assert mgr.state is RiskState.MANUAL_HALT

    def test_duplicate_halt_does_not_duplicate_events(self):
        mgr = _manager()
        mgr.halt(MONDAY, realised_equity=10_000.0)
        count = len(mgr.events)
        mgr.halt(MONDAY + timedelta(minutes=5), realised_equity=10_000.0)
        assert len(mgr.events) == count


class TestMultipleSimultaneousTriggers:
    def test_all_events_recorded_and_highest_precedence_state_reported(self):
        mgr = _manager(
            daily_loss_usd=100.0,
            equity_trail_giveback_usd=50.0,
            max_drawdown_usd=300.0,
        )
        _candle_obs(mgr, MONDAY, 10_000.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=5), 10_400.0)   # peak
        # One candle crosses all three: daily loss (-101 from the 10_000
        # baseline), trailing floor (10_350), and max drawdown (501 from peak).
        events = _candle_obs(mgr, MONDAY + timedelta(minutes=10), 9_899.0)
        types = [e.event_type for e in events]
        assert "MAX_DRAWDOWN_TRIGGERED" in types
        assert "EQUITY_TRAILING_TRIGGERED" not in types   # suppressed after max DD
        assert "DAILY_LOSS_TRIGGERED" in types
        assert mgr.state is RiskState.MAX_DRAWDOWN_TRIGGERED
        assert len(mgr.locks) >= 2   # both locks are individually active

    def test_evaluate_entry_reports_most_severe_reason(self):
        mgr = _manager(daily_loss_usd=100.0, max_drawdown_usd=300.0)
        _candle_obs(mgr, MONDAY, 10_000.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=5), 9_600.0)
        decision = mgr.evaluate_entry(MONDAY + timedelta(minutes=10),
                                      realised_equity=9_600.0, floating_pnl=0.0)
        assert decision.reason_code == "RISK_MAX_DRAWDOWN"


class TestPerTradeRiskCeiling:
    def test_usd_limit_blocks_oversized_trade(self):
        mgr = _manager(max_risk_per_trade_usd=50.0)
        decision = mgr.check_trade_risk(MONDAY, proposed_risk_usd=60.0,
                                        current_equity=10_000.0)
        assert decision.allowed is False
        assert decision.reason_code == "MAX_RISK_PER_TRADE_USD"
        assert decision.threshold_value == pytest.approx(50.0)

    def test_within_limit_allowed_and_not_locked(self):
        mgr = _manager(max_risk_per_trade_usd=50.0)
        decision = mgr.check_trade_risk(MONDAY, proposed_risk_usd=49.99,
                                        current_equity=10_000.0)
        assert decision.allowed is True
        assert mgr.state is RiskState.ACTIVE   # rejection is not a lock

    def test_pct_limit_measured_against_current_equity(self):
        mgr = _manager(max_risk_per_trade_pct=0.5)
        decision = mgr.check_trade_risk(MONDAY, proposed_risk_usd=51.0,
                                        current_equity=10_000.0)
        assert decision.allowed is False
        assert decision.reason_code == "MAX_RISK_PER_TRADE_PCT"
        assert decision.threshold_value == pytest.approx(50.0)

    def test_or_semantics_blocks_on_either_limit(self):
        mgr = _manager(max_risk_per_trade_usd=100.0, max_risk_per_trade_pct=0.5)
        # 80 USD > 50 (0.5% of 10k) but < 100 USD → blocked by pct form
        decision = mgr.check_trade_risk(MONDAY, proposed_risk_usd=80.0,
                                        current_equity=10_000.0)
        assert decision.allowed is False
        assert decision.reason_code == "MAX_RISK_PER_TRADE_PCT"


class TestOpenPositionPolicySignal:
    def test_stop_new_entries_policy_signal(self):
        mgr = _manager(open_position_action="STOP_NEW_ENTRIES", daily_loss_usd=100.0)
        _candle_obs(mgr, MONDAY, 10_000.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=5), 9_800.0)
        assert mgr.close_all_requested() is False
        event = mgr.events[-1]
        assert event.action == "STOP_NEW_ENTRIES"

    def test_close_all_policy_signal(self):
        mgr = _manager(open_position_action="CLOSE_ALL", daily_loss_usd=100.0)
        _candle_obs(mgr, MONDAY, 10_000.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=5), 9_800.0)
        assert mgr.close_all_requested() is True

    def test_record_forced_closures_updates_counters_and_streak(self):
        class FakeClosed:
            def __init__(self, setup_id, net):
                self.setup_id = setup_id
                self.net_pnl_usd = net

        mgr = _manager(max_consecutive_losses=2)
        _candle_obs(mgr, MONDAY, 10_000.0)
        mgr.record_forced_closures(
            MONDAY + timedelta(minutes=5),
            [FakeClosed("A", -30.0), FakeClosed("B", -40.0)],
            realised_equity=9_930.0)
        assert mgr.forced_closures == 2
        assert mgr.loss_streak == 2
        assert mgr.state is RiskState.LOSS_STREAK_REACHED
        force_events = [e for e in mgr.events if e.event_type == "FORCE_CLOSE"]
        assert len(force_events) == 1
        assert force_events[0].affected_positions == ["A", "B"]


class TestPersistence:
    def test_snapshot_and_restore_round_trip_preserves_locks(self):
        mgr = _manager(daily_loss_usd=100.0, equity_trail_giveback_usd=200.0,
                       config_fingerprint="acct-1")
        _candle_obs(mgr, MONDAY, 10_000.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=5), 10_500.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=10), 10_290.0)  # trailing lock

        restored = _manager(daily_loss_usd=100.0, equity_trail_giveback_usd=200.0,
                            config_fingerprint="acct-1")
        restored.restore_state(mgr.snapshot_state())
        assert restored.state is RiskState.EQUITY_TRAILING_TRIGGERED
        assert restored.peak_equity == pytest.approx(10_500.0)
        assert restored.floor_equity == pytest.approx(10_300.0)
        assert restored.summary()["event_count"] == mgr.summary()["event_count"]

    def test_save_and_load_state_file_round_trip(self, tmp_path):
        mgr = _manager(daily_loss_usd=100.0, config_fingerprint="acct-1")
        _candle_obs(mgr, MONDAY, 10_000.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=5), 9_800.0)
        path = mgr.save_state(tmp_path / "risk_state.json")

        revived = _manager(daily_loss_usd=100.0, config_fingerprint="acct-1")
        revived.load_state(path)
        assert revived.state is RiskState.DAILY_LOSS_REACHED
        # Restart while locked: entry gate must stay blocked after recovery.
        assert revived.evaluate_entry(MONDAY + timedelta(minutes=10),
                                      realised_equity=9_800.0,
                                      floating_pnl=0.0).allowed is False

    def test_corrupt_state_fails_safe(self, tmp_path):
        path = tmp_path / "corrupt.json"
        path.write_text("{not json", encoding="utf-8")
        mgr = _manager()
        with pytest.raises(RiskStateError, match="Unreadable"):
            mgr.load_state(path)

    def test_wrong_schema_version_rejected(self):
        mgr = _manager()
        with pytest.raises(RiskStateError, match="schema version"):
            mgr.restore_state({"schema_version": 999})

    def test_account_mismatch_rejected(self):
        mgr = _manager(config_fingerprint="acct-1")
        mgr._daily_baseline = 10_000.0
        other = _manager(config_fingerprint="acct-2")
        with pytest.raises(RiskStateError, match="different account"):
            other.restore_state(mgr.snapshot_state())

    def test_weakened_high_water_mark_rejected(self):
        mgr = _manager()
        _candle_obs(mgr, MONDAY, 10_000.0)
        _candle_obs(mgr, MONDAY + timedelta(minutes=5), 11_000.0)
        # The restoring manager has itself already observed a higher peak;
        # adopting the lower persisted value would weaken protection.
        stronger = _manager()
        _candle_obs(stronger, MONDAY, 10_000.0)
        _candle_obs(stronger, MONDAY + timedelta(minutes=5), 10_800.0)
        state = mgr.snapshot_state()          # peak 11_000
        state["peak_equity"] = 10_500.0       # tampered below the local peak
        with pytest.raises(RiskStateError, match="refusing to weaken"):
            stronger.restore_state(state)

    def test_missing_state_file_fails_safe(self, tmp_path):
        mgr = _manager()
        with pytest.raises(RiskStateError, match="not found"):
            mgr.load_state(tmp_path / "missing.json")

    def test_atomic_save_leaves_no_tmp_file(self, tmp_path):
        mgr = _manager()
        path = mgr.save_state(tmp_path / "state.json")
        assert path.is_file()
        assert not (tmp_path / "state.json.tmp").exists()


class TestManagerIgnoresStrategyAndMT5:
    def test_manager_module_has_no_mt5_or_strategy_imports(self):
        """The risk manager must stay independent of MT5 and the strategy
        layer (requirement 9): no such imports may appear in the module."""
        import ast

        import mt5_liquidity.risk.account_risk as module

        tree = ast.parse(open(module.__file__, encoding="utf-8").read())
        imported_names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported_names.append(node.module or "")
        for name in imported_names:
            lowered = name.lower()
            assert "metatrader5" not in lowered, name
            assert "strategy" not in lowered, name
            assert "mt5_adapter" not in lowered, name
