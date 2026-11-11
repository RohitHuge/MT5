"""Regression test: console output must not crash on cp1252 consoles.

Discovered during the Phase 2 sample run: print_header's Unicode arrow
("→") raised UnicodeEncodeError on a Windows cp1252 stdout, aborting the CLI
after the backtest had already completed and before the report was saved.
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

from mt5_liquidity.backtest.engine import BacktestResult, SymbolMeta
from mt5_liquidity.backtest.ledger import LedgerStats
from mt5_liquidity.backtest.positions import ClosedPosition
from mt5_liquidity.config import BacktestConfig
from mt5_liquidity.reporting.analytics import build_analytics_bundle
from mt5_liquidity.reporting.console import ensure_printable_stdout
from mt5_liquidity.reporting.metrics import print_analytics_summary, print_backtest_report

UTC = timezone.utc
TS = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def _result() -> BacktestResult:
    trade = ClosedPosition(
        setup_id="s1", direction="BUY", fill_price=2000.0, close_price=2002.0,
        tp_price=2004.0, sl_price=1998.0, lots=0.1, pnl_usd=20.0, exit_reason="TP",
        fill_candle_index=1, close_candle_index=3, fill_ts=TS,
        close_ts=TS + timedelta(minutes=15), bars_held=2,
        value_per_point_per_lot=1.0, point=0.01, commission_usd=1.0,
    )
    stats = LedgerStats(
        total_trades=1, winners=1, losers=0, win_rate=1.0, gross_profit=20.0,
        gross_loss=0.0, total_commission=1.0, net_pnl=19.0, profit_factor=20.0,
        avg_win=19.0, avg_loss=0.0, max_drawdown_usd=0.0, max_drawdown_pct=0.0,
        final_equity=10019.0,
    )
    return BacktestResult(
        setups_total=2, setups_entry_ready=1, setups_skipped_max_pos=0,
        setups_skipped_daily_loss=0, setups_skipped_sizing=0,
        setups_skipped_no_next_bar=0, trades_opened=1, closed_positions=[trade],
        force_closed=0, stats=stats, equity_curve=[(TS, 10019.0)],
        rejected_sizings=[],
    )


def test_reports_print_on_cp1252_stdout(capsys):
    """Simulate a cp1252-only console; printing must survive, not raise."""
    buffer = io.BytesIO()
    cp1252_stdout = io.TextIOWrapper(buffer, encoding="cp1252", line_buffering=True)

    import sys

    original = sys.stdout
    sys.stdout = cp1252_stdout
    try:
        ensure_printable_stdout()
        result = _result()
        print_backtest_report(result, "XAUUSD", "2026-11-09", "2026-11-10")
        print_analytics_summary(build_analytics_bundle(result, BacktestConfig()))
    finally:
        sys.stdout = original

    output = buffer.getvalue().decode("cp1252", errors="replace")
    assert "BACKTEST REPORT" in output
    assert "TIME ANALYTICS" in output
