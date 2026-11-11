from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from mt5_liquidity.backtest.engine import SymbolMeta, run_backtest
from mt5_liquidity.backtest.config import BacktestConfig
from mt5_liquidity.backtest.execution import FillResult
from mt5_liquidity.backtest.positions import OpenPosition
from mt5_liquidity.data.candles import Candle


def test_utc_day_key_normalizes_non_utc_aware_timestamp():
    # 00:30 at UTC+02 is 22:30 UTC on the previous calendar day.
    ts = datetime(2025, 1, 2, 0, 30, tzinfo=timezone(timedelta(hours=2)))
    assert ts.astimezone(timezone.utc).date().isoformat() == "2025-01-01"
