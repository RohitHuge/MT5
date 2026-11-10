"""Unit tests for symbol metadata validation (Fix 6).

validate_symbol_meta must reject any metadata that would cause the engine to
produce misleading results, and must accept valid values without raising.
"""
from __future__ import annotations

import pytest

from mt5_liquidity.backtest.engine import SymbolMeta, validate_symbol_meta

VALID = SymbolMeta(
    point=0.01,
    tick_value=1.0,
    tick_size=0.01,
    volume_min=0.01,
    volume_max=100.0,
    volume_step=0.01,
)


def test_valid_xauusd_metadata_accepted():
    assert validate_symbol_meta(VALID) is None


def test_valid_metadata_with_symbol_label():
    assert validate_symbol_meta(VALID, "XAUUSD") is None


def test_zero_point_rejected():
    bad = SymbolMeta(**{**VALID.__dict__, "point": 0.0})
    err = validate_symbol_meta(bad, "TEST")
    assert err is not None
    assert "point" in err


def test_negative_point_rejected():
    bad = SymbolMeta(**{**VALID.__dict__, "point": -0.01})
    assert validate_symbol_meta(bad) is not None


def test_zero_tick_size_rejected():
    bad = SymbolMeta(**{**VALID.__dict__, "tick_size": 0.0})
    err = validate_symbol_meta(bad)
    assert err is not None
    assert "tick_size" in err


def test_zero_tick_value_rejected():
    bad = SymbolMeta(**{**VALID.__dict__, "tick_value": 0.0})
    err = validate_symbol_meta(bad)
    assert err is not None
    assert "tick_value" in err


def test_zero_volume_min_rejected():
    bad = SymbolMeta(**{**VALID.__dict__, "volume_min": 0.0})
    err = validate_symbol_meta(bad)
    assert err is not None
    assert "volume_min" in err


def test_zero_volume_step_rejected():
    bad = SymbolMeta(**{**VALID.__dict__, "volume_step": 0.0})
    err = validate_symbol_meta(bad)
    assert err is not None
    assert "volume_step" in err


def test_zero_volume_max_rejected():
    bad = SymbolMeta(**{**VALID.__dict__, "volume_max": 0.0})
    err = validate_symbol_meta(bad)
    assert err is not None
    assert "volume_max" in err


def test_volume_min_greater_than_max_rejected():
    bad = SymbolMeta(**{**VALID.__dict__, "volume_min": 50.0, "volume_max": 10.0})
    err = validate_symbol_meta(bad)
    assert err is not None
    assert "volume_min" in err


def test_volume_step_greater_than_max_rejected():
    bad = SymbolMeta(**{**VALID.__dict__, "volume_step": 200.0, "volume_max": 100.0})
    err = validate_symbol_meta(bad)
    assert err is not None
    assert "volume_step" in err


def test_error_message_includes_symbol_name():
    bad = SymbolMeta(**{**VALID.__dict__, "point": 0.0})
    err = validate_symbol_meta(bad, "EURUSD")
    assert "EURUSD" in err


def test_engine_raises_on_invalid_metadata():
    """Engine must refuse to run with bad metadata (Fix 6 integration)."""
    from unittest.mock import patch
    from datetime import datetime, timezone, timedelta
    from mt5_liquidity.backtest.engine import run_backtest
    from mt5_liquidity.config import BacktestConfig
    from mt5_liquidity.data.candles import Candle

    cfg = BacktestConfig()
    candles = [
        Candle(timestamp=datetime(2025, 1, 6, tzinfo=timezone.utc) + timedelta(minutes=5*i),
               open=2000.0, high=2005.0, low=1995.0, close=2000.0)
        for i in range(5)
    ]
    bad_meta = SymbolMeta(point=0.0, tick_value=1.0, tick_size=0.01,
                          volume_min=0.01, volume_max=100.0, volume_step=0.01)

    with patch("mt5_liquidity.backtest.engine.run_liquidity_sweep_strategy", return_value=[]):
        with pytest.raises(ValueError, match="Invalid symbol metadata"):
            run_backtest([], candles, cfg, bad_meta)
