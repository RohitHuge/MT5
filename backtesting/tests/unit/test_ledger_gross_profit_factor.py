from datetime import datetime, timezone
import pytest

from mt5_liquidity.backtest.ledger import TradeLedger
from mt5_liquidity.backtest.positions import ClosedPosition


def trade(gross, commission):
    ts = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return ClosedPosition(
        setup_id="T", direction="BUY", fill_price=100.0, close_price=101.0,
        tp_price=110.0, sl_price=90.0, lots=0.1, pnl_usd=gross,
        exit_reason="TP", fill_candle_index=0, close_candle_index=1,
        fill_ts=ts, close_ts=ts, bars_held=1,
        value_per_point_per_lot=1.0, point=0.01,
        commission_usd=commission,
    )


def test_profit_factor_is_explicitly_gross_while_net_includes_commission():
    ledger = TradeLedger(initial_equity=1000.0)
    ledger.record(trade(100.0, 20.0))
    ledger.record(trade(-50.0, 10.0))
    stats = ledger.compute_stats()
    assert stats.profit_factor == pytest.approx(2.0)
    assert stats.net_pnl == pytest.approx(20.0)
    assert "gross" in stats.cost_note.lower()
