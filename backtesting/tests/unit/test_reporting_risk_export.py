"""Tests for Phase 3 risk reporting integration in the report exporter."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone

from mt5_liquidity.backtest.engine import BacktestResult, MarkToMarketPoint, SymbolMeta
from mt5_liquidity.backtest.ledger import LedgerStats
from mt5_liquidity.backtest.positions import ClosedPosition
from mt5_liquidity.config import AccountRiskConfig, BacktestConfig
from mt5_liquidity.risk.account_risk import RiskEvent, RiskState
from mt5_liquidity.reporting.exporter import (
    REPORT_SCHEMA_VERSION,
    export_backtest_report,
)

UTC = timezone.utc
TS = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def _trade(pnl: float, exit_reason: str = "SL") -> ClosedPosition:
    return ClosedPosition(
        setup_id="R1", direction="SELL", fill_price=2000.0, close_price=2010.0,
        tp_price=1980.0, sl_price=2015.0, lots=0.05, pnl_usd=pnl,
        exit_reason=exit_reason, fill_candle_index=1, close_candle_index=3,
        fill_ts=TS, close_ts=TS + timedelta(minutes=15), bars_held=2,
        value_per_point_per_lot=1.0, point=0.01, commission_usd=1.0,
    )


def _result(trades, events) -> BacktestResult:
    net = sum(t.net_pnl_usd for t in trades)
    stats = LedgerStats(
        total_trades=len(trades),
        winners=sum(1 for t in trades if t.net_pnl_usd > 0),
        losers=sum(1 for t in trades if t.net_pnl_usd <= 0),
        win_rate=0.0, gross_profit=0.0, gross_loss=min(net, 0.0),
        total_commission=0.0, net_pnl=net, profit_factor=0.0,
        avg_win=0.0, avg_loss=0.0, max_drawdown_usd=0.0, max_drawdown_pct=0.0,
        final_equity=10_000.0 + net,
    )
    return BacktestResult(
        setups_total=1, setups_entry_ready=1, setups_skipped_max_pos=0,
        setups_skipped_daily_loss=0, setups_skipped_sizing=0,
        setups_skipped_no_next_bar=0, trades_opened=1,
        closed_positions=trades, force_closed=0, stats=stats,
        equity_curve=[(TS + timedelta(minutes=15), 10_000.0 + net)],
        rejected_sizings=[],
        mark_to_market_curve=[
            MarkToMarketPoint(TS, 10_000.0, 0.0, 10_000.0, 0),
            MarkToMarketPoint(TS + timedelta(minutes=15), 10_000.0 + net, 0.0,
                              10_000.0 + net, 0),
        ],
        setups_skipped_risk=2,
        risk_blocked_by_reason={"RISK_DAILY_LOSS": 2},
        risk_events=events,
        risk_summary={
            "enabled": True,
            "final_state": RiskState.DAILY_LOSS_REACHED.value,
            "active_locks": [RiskState.DAILY_LOSS_REACHED.value],
            "locks_triggered": [RiskState.DAILY_LOSS_REACHED.value],
            "blocked_by_reason": {"RISK_DAILY_LOSS": 2},
            "forced_closures": 1,
            "peak_equity": 10_000.0,
            "floor_equity": None,
            "daily_baseline": 10_000.0,
            "loss_streak": 1,
            "open_position_action": "CLOSE_ALL",
            "event_count": len(events),
        },
    )


def _risk_event(event_type: str = "DAILY_LOSS_TRIGGERED") -> RiskEvent:
    return RiskEvent(
        timestamp=TS + timedelta(minutes=15),
        event_type=event_type,
        state=RiskState.DAILY_LOSS_REACHED.value,
        reason="Daily loss (realised + floating) reached configured limit",
        realised_equity=9_900.0, floating_pnl=0.0, mtm_equity=9_900.0,
        daily_pnl=-100.0, threshold_value=50.0, observed_value=100.0,
        peak_equity=10_000.0, floor_equity=None, open_positions=1,
        affected_positions=["R1"], action="CLOSE_ALL",
    )


def _export(tmp_path, result, account_risk: AccountRiskConfig | None = None):
    cfg = BacktestConfig()
    if account_risk is not None:
        cfg.account_risk = account_risk
    return export_backtest_report(
        result=result, symbol="XAUUSD", from_date="2026-11-09",
        to_date="2026-11-10", config=cfg,
        symbol_meta=SymbolMeta(0.01, 1.0, 0.01, 0.01, 100.0, 0.01),
        reports_dir=tmp_path,
        run_timestamp=datetime(2026, 11, 10, 18, 30, tzinfo=UTC),
    )


def test_risk_events_csv_written_with_structured_columns(tmp_path):
    event = _risk_event()
    report_dir = _export(tmp_path, _result([_trade(-51.0)], [event]))
    with (report_dir / "risk_events.csv").open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    row = rows[0]
    assert row["event_type"] == "DAILY_LOSS_TRIGGERED"
    assert row["state"] == "DAILY_LOSS_REACHED"
    assert row["threshold_value"] == "50.0"
    assert row["observed_value"] == "100.0"
    assert row["daily_pnl"] == "-100.0"
    assert row["affected_positions"] == "R1"
    assert row["action"] == "CLOSE_ALL"


def test_risk_events_csv_empty_when_manager_disabled(tmp_path):
    report_dir = _export(tmp_path, _result([_trade(-5.0)], []))
    with (report_dir / "risk_events.csv").open(newline="", encoding="utf-8") as fh:
        assert len(list(csv.DictReader(fh))) == 0


def test_summary_json_contains_risk_section(tmp_path):
    report_dir = _export(tmp_path, _result([_trade(-51.0)], [_risk_event()]))
    summary = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
    risk = summary["risk"]
    assert risk["enabled"] is True
    assert risk["final_state"] == "DAILY_LOSS_REACHED"
    assert risk["forced_closures"] == 1
    assert risk["blocked_by_reason"] == {"RISK_DAILY_LOSS": 2}


def test_metadata_contains_risk_policy_and_schema_v3(tmp_path):
    cfg = AccountRiskConfig(enabled=True, daily_loss_pct=2.0,
                            open_position_action="CLOSE_ALL")
    report_dir = _export(tmp_path, _result([_trade(-51.0)], [_risk_event()]), cfg)
    metadata = json.loads((report_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["schema_version"] == REPORT_SCHEMA_VERSION == 3
    assert metadata["risk"]["enabled"] is True
    assert metadata["risk"]["open_position_action"] == "CLOSE_ALL"
    # Full thresholds travel inside the config section (no secrets there).
    assert metadata["config"]["account_risk"]["daily_loss_pct"] == 2.0
    assert "mt5_password" not in metadata["config"]


def test_risk_exit_reasons_distinguishable_from_strategy_exits(tmp_path):
    trades = [
        _trade(-51.0, exit_reason="RISK_DAILY_LOSS"),
        _trade(-30.0, exit_reason="SL"),
        _trade(80.0, exit_reason="TP"),
    ]
    report_dir = _export(tmp_path, _result(trades, [_risk_event()]))
    with (report_dir / "trades.csv").open(newline="", encoding="utf-8") as fh:
        reasons = [r["exit_reason"] for r in csv.DictReader(fh)]
    assert "RISK_DAILY_LOSS" in reasons
    assert "SL" in reasons and "TP" in reasons
    # A protection stop is never counted as a strategy SL.
    assert reasons.count("RISK_DAILY_LOSS") == 1


def test_blocked_entry_counts_reconcile_with_summary(tmp_path):
    result = _result([_trade(-51.0)], [_risk_event()])
    report_dir = _export(tmp_path, result)
    summary = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["risk"]["blocked_by_reason"]["RISK_DAILY_LOSS"] \
        == sum(result.risk_blocked_by_reason.values())
