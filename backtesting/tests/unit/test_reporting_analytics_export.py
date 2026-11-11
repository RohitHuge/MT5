"""Tests for Phase 2 analytics integration in the report exporter."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone

from mt5_liquidity.backtest.engine import SymbolMeta
from mt5_liquidity.backtest.positions import ClosedPosition
from mt5_liquidity.backtest.ledger import LedgerStats
from mt5_liquidity.backtest.engine import BacktestResult, MarkToMarketPoint
from mt5_liquidity.config import BacktestConfig
from mt5_liquidity.reporting.exporter import (
    ANALYTICS_FILES,
    REPORT_SCHEMA_VERSION,
    export_backtest_report,
)

UTC = timezone.utc
BASE = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def _trade(pnl: float, fill: datetime, close: datetime, setup_id: str) -> ClosedPosition:
    return ClosedPosition(
        setup_id=setup_id, direction="SELL", fill_price=2000.0,
        close_price=1998.0, tp_price=1980.0, sl_price=2010.0,
        lots=0.1, pnl_usd=pnl, exit_reason="TP" if pnl > 0 else "SL",
        fill_candle_index=1, close_candle_index=3, fill_ts=fill, close_ts=close,
        bars_held=2, value_per_point_per_lot=1.0, point=0.01, commission_usd=1.0,
    )


def _result(trades: list[ClosedPosition]) -> BacktestResult:
    net = sum(t.net_pnl_usd for t in trades)
    stats = LedgerStats(
        total_trades=len(trades),
        winners=sum(1 for t in trades if t.net_pnl_usd > 0),
        losers=sum(1 for t in trades if t.net_pnl_usd <= 0),
        win_rate=(sum(1 for t in trades if t.net_pnl_usd > 0) / len(trades)) if trades else 0.0,
        gross_profit=sum(t.pnl_usd for t in trades if t.pnl_usd > 0),
        gross_loss=sum(t.pnl_usd for t in trades if t.pnl_usd <= 0),
        total_commission=sum(t.commission_usd for t in trades),
        net_pnl=net, profit_factor=2.0, avg_win=0.0, avg_loss=0.0,
        max_drawdown_usd=0.0, max_drawdown_pct=0.0,
        final_equity=10_000.0 + net,
    )
    mtm = [
        MarkToMarketPoint(BASE, 10_000.0, 0.0, 10_000.0, 0),
        MarkToMarketPoint(BASE + timedelta(minutes=5), 10_000.0, -20.0, 9_980.0, 1),
        MarkToMarketPoint(BASE + timedelta(minutes=10), 10_000.0 + net, 0.0,
                          10_000.0 + net, 0),
    ]
    return BacktestResult(
        setups_total=len(trades), setups_entry_ready=len(trades),
        setups_skipped_max_pos=0, setups_skipped_daily_loss=0,
        setups_skipped_sizing=0, setups_skipped_no_next_bar=0,
        trades_opened=len(trades), closed_positions=trades, force_closed=0,
        stats=stats, equity_curve=[(BASE + timedelta(minutes=10), 10_000.0 + net)],
        rejected_sizings=[], mark_to_market_curve=mtm,
    )


def _export(tmp_path, trades):
    return export_backtest_report(
        result=_result(trades), symbol="XAUUSD", from_date="2026-11-09",
        to_date="2026-11-10", config=BacktestConfig(),
        symbol_meta=SymbolMeta(0.01, 1.0, 0.01, 0.01, 100.0, 0.01),
        reports_dir=tmp_path,
        run_timestamp=datetime(2026, 11, 10, 18, 30, tzinfo=UTC),
    )


def test_analytics_dir_contains_all_schema_v2_files(tmp_path):
    trade = _trade(30.0, BASE, BASE + timedelta(minutes=15), "A1")
    report_dir = _export(tmp_path, [trade])
    a_dir = report_dir / "analytics"
    assert {p.name for p in a_dir.iterdir()} == set(ANALYTICS_FILES)


def test_hourly_csv_has_24_rows_and_reconciles(tmp_path):
    trades = [
        _trade(30.0, BASE, BASE + timedelta(minutes=15), "A1"),
        _trade(-12.0, BASE.replace(hour=15), BASE.replace(hour=15, minute=20), "A2"),
    ]
    report_dir = _export(tmp_path, trades)
    with (report_dir / "analytics" / "hourly_performance.csv").open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 24
    assert sum(int(r["trade_count"]) for r in rows) == 2
    # net: 30 - 1 commission + (-12 - 1) = 16
    assert sum(float(r["net_pnl"]) for r in rows) == 16.0


def test_session_csv_counts_reconcile(tmp_path):
    trade = _trade(30.0, BASE.replace(hour=14), BASE.replace(hour=14, minute=15), "A1")
    report_dir = _export(tmp_path, [trade])
    with (report_dir / "analytics" / "session_performance.csv").open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert sum(int(r["trade_count"]) for r in rows) == 1
    sessions = {r["session"] for r in rows}
    assert {"TOKYO", "LONDON", "NEW_YORK", "LONDON_NY_OVERLAP", "OFF_SESSION"} == sessions


def test_duration_csv_buckets_present(tmp_path):
    trade = _trade(30.0, BASE, BASE + timedelta(minutes=45), "A1")
    report_dir = _export(tmp_path, [trade])
    with (report_dir / "analytics" / "duration_analysis.csv").open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["bucket"] for r in rows] == [
        "0-5m", "5-15m", "15-30m", "30-60m", "60-120m", "120m+"]
    assert sum(int(r["trade_count"]) for r in rows) == 1
    assert rows[3]["trade_count"] == "1"   # 45 min → 30-60m


def test_period_csvs_written_and_reconcile(tmp_path):
    trades = [
        _trade(30.0, BASE, BASE + timedelta(minutes=15), "A1"),
        _trade(-12.0, BASE + timedelta(days=1), BASE + timedelta(days=1, minutes=20), "A2"),
    ]
    report_dir = _export(tmp_path, trades)
    a_dir = report_dir / "analytics"
    for name in ("daily_performance.csv", "weekly_performance.csv", "monthly_performance.csv"):
        with (a_dir / name).open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        assert sum(int(r["trade_count"]) for r in rows) == 2
        assert abs(sum(float(r["net_pnl"]) for r in rows) - 16.0) < 0.01
    with (a_dir / "daily_performance.csv").open(newline="", encoding="utf-8") as fh:
        daily = list(csv.DictReader(fh))
    assert {r["period_id"] for r in daily} >= {"2026-11-09", "2026-11-10"}


def test_mark_to_market_equity_csv(tmp_path):
    trade = _trade(30.0, BASE, BASE + timedelta(minutes=15), "A1")
    report_dir = _export(tmp_path, [trade])
    with (report_dir / "analytics" / "mark_to_market_equity.csv").open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == [
            "timestamp_utc", "realised_equity", "floating_pnl",
            "mtm_equity", "open_positions"]
        rows = list(reader)
    assert len(rows) == 3
    assert float(rows[1]["floating_pnl"]) == -20.0
    # final MTM point = realised after the trade's net P&L (30 gross - 1 commission)
    assert float(rows[2]["mtm_equity"]) == 10_029.0


def test_analytics_summary_json_schema_and_reconciliation(tmp_path):
    trade = _trade(30.0, BASE, BASE + timedelta(minutes=15), "A1")
    report_dir = _export(tmp_path, [trade])
    summary = json.loads(
        (report_dir / "analytics" / "analytics_summary.json").read_text(encoding="utf-8"))
    assert summary["schema_version"] == 1
    assert summary["timezone_policy"] == "UTC"
    assert summary["attribution"]
    assert len(summary["sessions"]) == 3
    assert summary["duration_summary"]["avg_duration_minutes"] == 15.0
    assert summary["reconciliation"]["counts_reconciled"] is True
    assert summary["reconciliation"]["net_pnl_reconciled"] is True
    assert summary["reconciliation"]["final_equity_reconciled"] is True
    assert summary["mark_to_market_drawdown"]["max_drawdown_usd"] == 20.0


def test_metadata_has_analytics_section_and_schema_v2(tmp_path):
    report_dir = _export(tmp_path, [])
    metadata = json.loads((report_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["schema_version"] == REPORT_SCHEMA_VERSION == 2
    analytics = metadata["analytics"]
    assert analytics["schema_version"] == 1
    assert analytics["timezone_policy"] == "UTC"
    assert analytics["attribution"]
    assert {s["name"] for s in analytics["sessions"]} == {"TOKYO", "LONDON", "NEW_YORK"}


def test_empty_dataset_produces_safe_files(tmp_path):
    report_dir = _export(tmp_path, [])
    a_dir = report_dir / "analytics"
    for name in ANALYTICS_FILES:
        assert (a_dir / name).is_file()
    summary = json.loads((a_dir / "analytics_summary.json").read_text(encoding="utf-8"))
    assert summary["duration_summary"]["avg_duration_minutes"] is None
    assert summary["reconciliation"]["counts_reconciled"] is True
    assert summary["headline"].get("best_hour_utc") is None


def test_no_secrets_in_analytics_outputs(tmp_path):
    cfg = BacktestConfig(mt5_login=999, mt5_password="super-secret")
    report_dir = export_backtest_report(
        result=_result([_trade(5.0, BASE, BASE + timedelta(minutes=5), "A1")]),
        symbol="XAUUSD", from_date="2026-11-09", to_date="2026-11-10",
        config=cfg, symbol_meta=SymbolMeta(0.01, 1.0, 0.01, 0.01, 100.0, 0.01),
        reports_dir=tmp_path,
        run_timestamp=datetime(2026, 11, 10, 18, 30, tzinfo=UTC),
    )
    for path in report_dir.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert "super-secret" not in text
    # The analytics summary specifically must not carry credentials either.
    summary = (report_dir / "analytics" / "analytics_summary.json").read_text(encoding="utf-8")
    assert "mt5_login" not in summary
    assert "mt5_password" not in summary
