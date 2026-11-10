"""Unit tests for timezone handling — Task 7."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mt5_liquidity.data.candles import Candle
from mt5_liquidity.data.validation import validate_candles
from tests.fixtures.candle_fixtures import BASE_TIME, make_candle


def test_naive_timestamp_raises_on_candle_creation():
    """Candle.__post_init__ must raise ValueError for naive timestamps."""
    with pytest.raises(ValueError, match="UTC-aware"):
        Candle(datetime(2025, 1, 6, 0, 0), 100, 105, 99, 103)


def test_aware_timestamp_accepted():
    """Candle with explicit UTC tzinfo must be accepted without error."""
    c = Candle(datetime(2025, 1, 6, 0, 0, tzinfo=timezone.utc), 100, 105, 99, 103)
    assert c.timestamp.tzinfo is not None


def test_epoch_seconds_converts_to_utc():
    """
    Simulate what MT5Adapter does: datetime.fromtimestamp(epoch, tz=timezone.utc).
    A known epoch value must yield the correct UTC datetime.
    epoch=1735084800 = 2024-12-25 00:00:00 UTC
    """
    epoch = 1735084800
    ts = datetime.fromtimestamp(epoch, tz=timezone.utc)
    assert ts.year == 2024
    assert ts.month == 12
    assert ts.day == 25
    assert ts.hour == 0
    assert ts.tzinfo == timezone.utc


def test_validate_candles_warns_on_naive_timestamp():
    """
    If a candle somehow has a naive timestamp (bypassing __post_init__),
    validate_candles must add a warning.
    We bypass Candle.__post_init__ using object.__setattr__ to simulate the scenario.
    """
    c = make_candle(BASE_TIME, 100, 105, 99, 103)
    # Force naive timestamp for test purposes, bypassing __post_init__
    object.__setattr__(c, "timestamp", datetime(2025, 1, 6, 0, 0))
    result = validate_candles([c], "M5")
    naive_warnings = [w for w in result.warnings if "naive" in w.lower()]
    assert len(naive_warnings) >= 1


def test_validate_catches_mixed_aware_and_naive():
    """Series with one naive and one aware timestamp: naive one gets a warning."""
    aware = make_candle(BASE_TIME, 100, 105, 99, 103)
    naive = make_candle(BASE_TIME, 100, 105, 99, 103)
    object.__setattr__(naive, "timestamp", datetime(2025, 1, 6, 0, 5))
    result = validate_candles([aware, naive], "M5")
    naive_warnings = [w for w in result.warnings if "naive" in w.lower()]
    assert len(naive_warnings) >= 1
