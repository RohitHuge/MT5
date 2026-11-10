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


def _load_config():
    """Load config pointing at the POC .env for credentials."""
    from pathlib import Path
    from dotenv import load_dotenv
    from mt5_liquidity.config import load_config

    env_path = Path(__file__).parents[3] / "mt5-connectivity-poc" / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=True)
    return load_config()


def _recent_week() -> tuple[datetime, datetime]:
    """
    Return (from_dt, to_dt) for recent 5 trading days available from MT5.
    Uses copy_rates_from_pos to find what dates the broker has.
    """
    import MetaTrader5 as mt5
    # Fetch last 500 M5 bars to find actual coverage
    rates = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_M5, 0, 500)
    if rates is None or len(rates) == 0:
        # Fallback: last 7 days
        from datetime import timedelta
        to_dt = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        from_dt = to_dt - timedelta(days=7)
        return from_dt, to_dt
    # Use first and last bar timestamps
    from_dt = datetime.fromtimestamp(int(rates[0]["time"]), tz=timezone.utc)
    to_dt   = datetime.fromtimestamp(int(rates[-1]["time"]), tz=timezone.utc)
    return from_dt, to_dt


@pytest.mark.skipif(not mt5_available, reason="MT5 terminal not running")
def test_mt5_connection():
    import MetaTrader5 as mt5
    assert mt5.initialize(), f"MT5 init failed: {mt5.last_error()}"
    info = mt5.terminal_info()
    assert info is not None
    assert info.connected
    print(f"\n  MT5 build={info.build}  connected={info.connected}")
    mt5.shutdown()


@pytest.mark.skipif(not mt5_available, reason="MT5 terminal not running")
def test_fetch_small_range():
    """Fetch recent XAUUSD M5 data from broker and verify non-empty result."""
    from mt5_liquidity.data.mt5_adapter import MT5Adapter
    from mt5_liquidity.data.history import fetch_m5

    cfg = _load_config()
    adapter = MT5Adapter(cfg)
    assert adapter.connect(), "MT5 connection failed"

    from_dt, to_dt = _recent_week()
    candles = fetch_m5(adapter, "XAUUSD", from_dt, to_dt)
    adapter.disconnect()

    print(f"\n  Fetched {len(candles)} M5 candles [{from_dt.date()} -> {to_dt.date()}]")
    assert len(candles) > 0
    assert all(c.timeframe == "M5" for c in candles)
    assert all(c.timestamp.tzinfo is not None for c in candles), "All timestamps must be UTC-aware"


@pytest.mark.skipif(not mt5_available, reason="MT5 terminal not running")
def test_m15_comparison():
    """
    Task 5 — Fetch M5 and native M15, derive M15 from M5, compare.
    Asserts coverage >= 95%. Prints full comparison result.
    """
    from mt5_liquidity.data.mt5_adapter import MT5Adapter
    from mt5_liquidity.data.history import fetch_m5, fetch_m15_native, compare_m15
    from mt5_liquidity.data.candles import aggregate_m5_to_m15
    from mt5_liquidity.data.validation import validate_candles

    cfg = _load_config()
    adapter = MT5Adapter(cfg)
    assert adapter.connect(), "MT5 connection failed"

    # Use broker's actual recent data range
    from_dt, to_dt = _recent_week()

    m5_candles = fetch_m5(adapter, "XAUUSD", from_dt, to_dt)
    native_m15 = fetch_m15_native(adapter, "XAUUSD", from_dt, to_dt)
    adapter.disconnect()

    print(f"\n  M5 candles fetched : {len(m5_candles)}")
    print(f"  Native M15 candles : {len(native_m15)}")

    # Validate M5 first
    val = validate_candles(m5_candles, "M5")
    print(f"  M5 validation passed={val.passed}  gaps={len(val.gaps)}  ohlc_violations={len(val.ohlc_violations)}")

    # Derive M15
    agg = aggregate_m5_to_m15(m5_candles)
    derived_m15 = agg.candles
    print(f"  Derived M15 candles: {len(derived_m15)}")
    if agg.incomplete_windows:
        print(f"  Incomplete windows : {len(agg.incomplete_windows)}")
        for w in agg.incomplete_windows[:5]:
            print(f"    {w['window_start'].isoformat()} — expected 3, got {w['got']}")

    # Compare
    result = compare_m15(derived_m15, native_m15)
    print(f"\n  M15 Comparison Results:")
    print(f"    Matched     : {result['matched']}")
    print(f"    Mismatched  : {len(result['mismatched'])}")
    print(f"    Coverage    : {result['coverage_pct']}%")

    if result["mismatched"]:
        print(f"\n  Mismatches (first 10):")
        for m in result["mismatched"][:10]:
            print(f"    {m['ts']}  derived={m['derived_ohlc']}  native={m['native_ohlc']}  reason={m['reason']}")

    assert result["coverage_pct"] >= 95.0, (
        f"M15 coverage {result['coverage_pct']}% < 95%. "
        f"Mismatches: {result['mismatched'][:3]}"
    )


@pytest.mark.skipif(not mt5_available, reason="MT5 terminal not running")
def test_symbol_metadata_no_hardcoding():
    """Symbol info must come from MT5, not hardcoded values."""
    from mt5_liquidity.data.mt5_adapter import MT5Adapter

    cfg = _load_config()
    adapter = MT5Adapter(cfg)
    assert adapter.connect()
    info = adapter.get_symbol_info("XAUUSD")
    adapter.disconnect()

    print(f"\n  XAUUSD metadata: {info}")
    assert info["digits"] > 0
    assert info["point"] > 0
    assert info["volume_min"] > 0
    assert info["contract_size"] > 0
