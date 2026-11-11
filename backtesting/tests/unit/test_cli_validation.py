"""CLI regression tests for the backtest input validation gate."""
from __future__ import annotations

from argparse import Namespace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from mt5_liquidity.data.candles import Candle
from mt5_liquidity import cli


def _args() -> Namespace:
    return Namespace(
        symbol="XAUUSD",
        pivot_left=2,
        pivot_right=2,
        risk_pct=0.5,
        rr_target=2.0,
        max_positions=1,
        max_daily_loss_pct=3.0,
        equity=10_000.0,
        from_date="2026-08-13",
        to_date="2026-08-14",
    )


def _valid_m5() -> list[Candle]:
    start = datetime(2026, 8, 13, 0, 0, tzinfo=timezone.utc)
    return [
        Candle(start.replace(minute=minute), 100.0, 101.0, 99.0, 100.0)
        for minute in (0, 5, 10)
    ]


def _patch_common(monkeypatch, m5: list[Candle]) -> tuple[Mock, Mock]:
    cfg = SimpleNamespace(cache_dir="cache")
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(cli, "CandleCache", lambda _directory: SimpleNamespace(
        load=lambda *_args: m5,
        save=Mock(),
    ))
    run_backtest = Mock()
    connect = Mock(return_value=True)
    adapter = SimpleNamespace(
        connect=connect,
        disconnect=Mock(),
        get_symbol_info=Mock(return_value={
            "point": 0.01,
            "tick_value": 1.0,
            "tick_size": 0.01,
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
        }),
    )

    class AdapterFactory:
        def __init__(self, _cfg):
            pass

        def connect(self):
            return adapter.connect()

        def disconnect(self):
            return adapter.disconnect()

        def get_symbol_info(self, symbol):
            return adapter.get_symbol_info(symbol)

    monkeypatch.setattr(cli, "MT5Adapter", AdapterFactory)

    # Ensure any accidental attempt to run a backtest is observable.
    from mt5_liquidity.backtest import engine
    monkeypatch.setattr(engine, "run_backtest", run_backtest)
    return run_backtest, connect


def test_cmd_backtest_rejects_invalid_m5_before_aggregation_or_mt5(monkeypatch, capsys):
    invalid = _valid_m5()
    invalid[1] = Candle(
        invalid[1].timestamp,
        open=100.0,
        high=98.0,  # invalid: high is below open and close
        low=97.0,
        close=99.0,
    )
    run_backtest, connect = _patch_common(monkeypatch, invalid)

    result = cli.cmd_backtest(_args())

    output = capsys.readouterr()
    assert result == 1
    assert "M5 data quality check failed" in output.err
    assert "Validation passed   : False" in output.out
    assert "OHLC violations" in output.out
    connect.assert_not_called()
    run_backtest.assert_not_called()


def test_cmd_backtest_continues_when_m5_validation_passes(monkeypatch):
    m5 = _valid_m5()
    run_backtest, connect = _patch_common(monkeypatch, m5)
    run_backtest.return_value = object()

    # Stop after the engine is invoked; the test is only for the input gate.
    class BacktestReached(Exception):
        pass

    from mt5_liquidity.backtest import engine

    def stop_after_run(**kwargs):
        run_backtest(**kwargs)
        raise BacktestReached

    monkeypatch.setattr(engine, "run_backtest", stop_after_run)

    import pytest
    with pytest.raises(BacktestReached):
        cli.cmd_backtest(_args())

    connect.assert_called_once()
    run_backtest.assert_called_once()
