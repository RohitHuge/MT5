from __future__ import annotations

import csv
import json
from datetime import datetime, timezone

import pytest

from mt5_liquidity.backtest.engine import BacktestResult, SymbolMeta
from mt5_liquidity.backtest.ledger import LedgerStats
from mt5_liquidity.backtest.positions import ClosedPosition
from mt5_liquidity.config import BacktestConfig
from mt5_liquidity.reporting.exporter import export_backtest_report


def _result() -> BacktestResult:
    ts = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    trade = ClosedPosition(
        setup_id="setup-1", direction="BUY", fill_price=2000.0,
        close_price=2002.0, tp_price=2004.0, sl_price=1998.0,
        lots=0.1, pnl_usd=20.0, exit_reason="TP",
        fill_candle_index=1, close_candle_index=3, fill_ts=ts,
        close_ts=ts, bars_held=2, value_per_point_per_lot=1.0,
        point=0.01, commission_usd=1.0,
    )
    stats = LedgerStats(
        total_trades=1, winners=1, losers=0, win_rate=1.0,
        gross_profit=20.0, gross_loss=0.0, total_commission=1.0,
        net_pnl=19.0, profit_factor=float("inf"), avg_win=19.0,
        avg_loss=0.0, max_drawdown_usd=0.0, max_drawdown_pct=0.0,
        final_equity=10019.0,
    )
    return BacktestResult(
        setups_total=2, setups_entry_ready=1, setups_skipped_max_pos=0,
        setups_skipped_daily_loss=0, setups_skipped_sizing=0,
        setups_skipped_no_next_bar=0, trades_opened=1,
        closed_positions=[trade], force_closed=0, stats=stats,
        equity_curve=[(ts, 10019.0)], rejected_sizings=[],
    )


def test_export_writes_complete_report_bundle(tmp_path):
    cfg = BacktestConfig(mt5_login=12345, mt5_password="must-not-leak")
    report_dir = export_backtest_report(
        result=_result(), symbol="XAUUSD", from_date="2026-11-01",
        to_date="2026-11-10", config=cfg,
        symbol_meta=SymbolMeta(0.01, 1.0, 0.01, 0.01, 100.0, 0.01),
        incomplete_m15_windows=[{
            "window_start": datetime(2026, 11, 2, tzinfo=timezone.utc),
            "expected": 3, "got": 2,
        }],
        reports_dir=tmp_path,
        run_timestamp=datetime(2026, 11, 10, 18, 30, tzinfo=timezone.utc),
    )

    expected = {
        "summary.json", "trades.csv", "equity_curve.csv",
        "metadata.json", "execution.log",
    }
    assert {p.name for p in report_dir.iterdir()} == expected

    summary = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["statistics"]["net_pnl"] == 19.0
    assert summary["statistics"]["profit_factor"] == "Infinity" or summary["statistics"]["profit_factor"] == "inf"

    with (report_dir / "trades.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["net_pnl_usd"] == "19.0"

    with (report_dir / "equity_curve.csv").open(newline="", encoding="utf-8") as handle:
        equity_rows = list(csv.DictReader(handle))
    assert equity_rows[0]["equity"] == "10019.0"

    metadata = json.loads((report_dir / "metadata.json").read_text(encoding="utf-8"))
    assert "mt5_password" not in metadata["config"]
    assert "mt5_login" not in metadata["config"]
    assert metadata["data_quality"]["incomplete_m15_windows"][0]["got"] == 2


def test_each_export_uses_a_unique_directory(tmp_path):
    kwargs = dict(
        result=_result(), symbol="XAUUSD", from_date="2026-11-01",
        to_date="2026-11-10", config=BacktestConfig(),
        symbol_meta=SymbolMeta(0.01, 1.0, 0.01, 0.01, 100.0, 0.01),
        reports_dir=tmp_path,
        run_timestamp=datetime(2026, 11, 10, 18, 30, tzinfo=timezone.utc),
    )
    first = export_backtest_report(**kwargs)
    second = export_backtest_report(**kwargs)
    assert first != second
    assert first.exists() and second.exists()


def test_naive_run_timestamp_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="timezone-aware"):
        export_backtest_report(
            result=_result(), symbol="XAUUSD", from_date="2026-11-01",
            to_date="2026-11-10", config=BacktestConfig(),
            symbol_meta=SymbolMeta(0.01, 1.0, 0.01, 0.01, 100.0, 0.01),
            reports_dir=tmp_path,
            run_timestamp=datetime(2026, 11, 10, 18, 30),
        )
