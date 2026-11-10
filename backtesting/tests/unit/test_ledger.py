"""Unit tests for TradeLedger and LedgerStats (backtest/ledger.py)."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta

from mt5_liquidity.backtest.ledger import TradeLedger
from mt5_liquidity.backtest.positions import ClosedPosition

BASE = datetime(2025, 1, 6, 0, 0, tzinfo=timezone.utc)
POINT = 0.01
VPP = 1.0


def _trade(pnl: float, direction: str = "BUY", setup_id: str = "T") -> ClosedPosition:
    return ClosedPosition(
        setup_id=setup_id,
        direction=direction,
        fill_price=2000.0,
        close_price=2000.0 + (pnl / 100),
        tp_price=2020.0,
        sl_price=1990.0,
        lots=0.1,
        pnl_usd=pnl,
        exit_reason="TP" if pnl > 0 else "SL",
        fill_candle_index=0,
        close_candle_index=5,
        fill_ts=BASE,
        close_ts=BASE + timedelta(minutes=25),
        bars_held=5,
        value_per_point_per_lot=VPP,
        point=POINT,
    )


def test_empty_ledger():
    ledger = TradeLedger(initial_equity=10_000.0)
    stats = ledger.compute_stats()
    assert stats.total_trades == 0
    assert stats.net_pnl == 0.0
    assert stats.final_equity == 10_000.0


def test_single_win():
    ledger = TradeLedger(initial_equity=10_000.0)
    ledger.record(_trade(100.0))
    stats = ledger.compute_stats()
    assert stats.total_trades == 1
    assert stats.winners == 1
    assert stats.losers == 0
    assert stats.win_rate == pytest.approx(1.0)
    assert stats.net_pnl == pytest.approx(100.0)
    assert stats.profit_factor == float("inf")


def test_single_loss():
    ledger = TradeLedger(initial_equity=10_000.0)
    ledger.record(_trade(-50.0))
    stats = ledger.compute_stats()
    assert stats.winners == 0
    assert stats.losers == 1
    assert stats.net_pnl == pytest.approx(-50.0)
    assert stats.profit_factor == pytest.approx(0.0)


def test_profit_factor():
    ledger = TradeLedger(initial_equity=10_000.0)
    ledger.record(_trade(200.0))
    ledger.record(_trade(100.0))
    ledger.record(_trade(-100.0))
    stats = ledger.compute_stats()
    # gross_profit=300, gross_loss=-100 → PF = 3.0
    assert stats.profit_factor == pytest.approx(3.0)


def test_max_drawdown():
    # Equity curve: 10000 → 10200 (peak) → 10100 → 9900 → 10100
    ledger = TradeLedger(initial_equity=10_000.0)
    ledger.record(_trade(200.0))
    ledger.record(_trade(-100.0))
    ledger.record(_trade(-200.0))  # equity = 9900; dd from 10200 = 300
    ledger.record(_trade(200.0))
    stats = ledger.compute_stats()
    assert stats.max_drawdown_usd == pytest.approx(300.0)
    assert stats.max_drawdown_pct == pytest.approx(300.0 / 10200.0 * 100.0, abs=1e-3)


def test_final_equity():
    ledger = TradeLedger(initial_equity=10_000.0)
    ledger.record(_trade(500.0))
    ledger.record(_trade(-200.0))
    stats = ledger.compute_stats()
    assert stats.final_equity == pytest.approx(10_300.0)


def test_win_rate():
    ledger = TradeLedger(initial_equity=10_000.0)
    for pnl in [100, 100, -50, -50, 100]:
        ledger.record(_trade(pnl))
    stats = ledger.compute_stats()
    assert stats.win_rate == pytest.approx(3 / 5)


def test_avg_win_avg_loss():
    ledger = TradeLedger(initial_equity=10_000.0)
    ledger.record(_trade(100.0))
    ledger.record(_trade(200.0))
    ledger.record(_trade(-50.0))
    ledger.record(_trade(-150.0))
    stats = ledger.compute_stats()
    assert stats.avg_win == pytest.approx(150.0)
    assert stats.avg_loss == pytest.approx(-100.0)
