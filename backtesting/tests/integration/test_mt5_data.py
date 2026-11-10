"""Integration tests — skipped automatically when MT5 terminal is not running."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

mt5_available = False
try:
    import MetaTrader5 as mt5
    if mt5.initialize():
        mt5_available = True
        mt5.shutdown()
except Exception:
    pass


@pytest.mark.skipif(not mt5_available, reason="MT5 terminal not running")
def test_mt5_connection():
    import MetaTrader5 as mt5
    assert mt5.initialize(), f"MT5 init failed: {mt5.last_error()}"
    info = mt5.terminal_info()
    assert info is not None
    assert info.connected
    mt5.shutdown()


@pytest.mark.skipif(not mt5_available, reason="MT5 terminal not running")
def test_fetch_small_range():
    """Fetch 5 days of XAUUSD M5 data and verify non-empty result."""
    import MetaTrader5 as mt5
    from mt5_liquidity.config import load_config
    from mt5_liquidity.data.mt5_adapter import MT5Adapter
    from mt5_liquidity.data.history import fetch_m5

    cfg = load_config()
    adapter = MT5Adapter(cfg)
    assert adapter.connect()

    from_dt = datetime(2025, 1, 6, tzinfo=timezone.utc)
    to_dt   = datetime(2025, 1, 10, tzinfo=timezone.utc)
    candles = fetch_m5(adapter, "XAUUSD", from_dt, to_dt)
    adapter.disconnect()

    assert len(candles) > 0
    assert all(c.timeframe == "M5" for c in candles)
    assert candles[0].timestamp >= from_dt
