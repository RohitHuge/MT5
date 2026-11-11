from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from mt5_liquidity.backtest.engine import SymbolMeta, run_backtest
from mt5_liquidity.config import BacktestConfig
from mt5_liquidity.data.candles import Candle


def test_engine_uses_utc_day_boundary_for_timezone_aware_candles():
    # 00:30 at UTC+02 is 22:30 UTC on the previous calendar day.
    from datetime import timezone as tz
    ts = datetime(2025, 1, 2, 0, 30, tzinfo=tz(timedelta(hours=2)))
    candle = Candle(timestamp=ts, open=2000, high=2001, low=1999, close=2000)
    config = BacktestConfig(initial_equity=10000)
    meta = SymbolMeta(point=0.01, tick_value=1, tick_size=0.01,
                      volume_min=0.01, volume_max=10, volume_step=0.01)
    with patch("mt5_liquidity.backtest.engine.run_liquidity_sweep_strategy", return_value=[]):
        result = run_backtest([], [candle], config, meta)
    assert result.mark_to_market_curve[0].timestamp == ts
    assert result.mark_to_market_curve[0].mtm_equity == pytest.approx(10000)
