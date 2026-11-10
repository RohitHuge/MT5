"""Unit tests for M5 MSS detection (Phase 3 P3.6–P3.10)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mt5_liquidity.data.candles import Candle
from mt5_liquidity.strategy.market_structure import detect_mss
from mt5_liquidity.strategy.models import Pivot, PivotType, ZoneType
from mt5_liquidity.strategy.sweeps import SweepSignal

BASE = datetime(2025, 1, 6, 8, 0, tzinfo=timezone.utc)
M5 = timedelta(minutes=5)
POINT = 0.01


def _ts(i: int) -> datetime:
    return BASE + i * M5


def _candle(i: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(timestamp=_ts(i), open=o, high=h, low=l, close=c, timeframe="M5")


def _pivot_low(index: int, confirmed_at: int, price: float) -> Pivot:
    return Pivot(
        index=index,
        confirmed_at_index=confirmed_at,
        timestamp=_ts(index),
        confirmed_at=_ts(confirmed_at),
        pivot_type=PivotType.LOW,
        price=price,
        candle_high=price + 1,
        candle_low=price,
    )


def _pivot_high(index: int, confirmed_at: int, price: float) -> Pivot:
    return Pivot(
        index=index,
        confirmed_at_index=confirmed_at,
        timestamp=_ts(index),
        confirmed_at=_ts(confirmed_at),
        pivot_type=PivotType.HIGH,
        price=price,
        candle_high=price,
        candle_low=price - 1,
    )


def _bsl_sweep(sweep_candle_index: int = 0) -> SweepSignal:
    return SweepSignal(
        zone_id="BSL_000000",
        zone_type=ZoneType.BSL,
        sweep_candle_index=sweep_candle_index,
        sweep_candle_ts=_ts(sweep_candle_index),
        wick_extreme=1010.5,
        close_price=1009.5,
        zone_upper=1010.1,
        zone_lower=1009.9,
        min_sweep_points=5,
    )


def _ssl_sweep(sweep_candle_index: int = 0) -> SweepSignal:
    return SweepSignal(
        zone_id="SSL_000000",
        zone_type=ZoneType.SSL,
        sweep_candle_index=sweep_candle_index,
        sweep_candle_ts=_ts(sweep_candle_index),
        wick_extreme=989.5,
        close_price=990.5,
        zone_upper=990.1,
        zone_lower=989.9,
        min_sweep_points=5,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_bearish_mss_detected():
    """BSL sweep → first M5 bar that closes below confirmed swing low → MSSSignal (BEARISH)."""
    # Swing low at index 2, confirmed at index 4 (right=2)
    pivot = _pivot_low(index=2, confirmed_at=4, price=995.0)
    # M5 candles: start searching from index 5
    candles = [_candle(i, 1000, 1001, 999, 1000) for i in range(10)]
    candles[5] = _candle(5, 999.0, 999.5, 994.0, 994.5)  # close < 995.0

    mss = detect_mss(
        m5_candles=candles,
        m5_pivots=[pivot],
        sweep=_bsl_sweep(),
        sweep_m5_index=5,
        mss_timeout_bars=10,
        point=POINT,
    )
    assert mss is not None
    assert mss.direction == "BEARISH"
    assert mss.break_candle_index == 5
    assert mss.broken_pivot_price == 995.0
    assert mss.bars_after_sweep == 0   # first bar is index 5, sweep_m5_index = 5


def test_bullish_mss_detected():
    """SSL sweep → first M5 bar that closes above confirmed swing high → MSSSignal (BULLISH)."""
    pivot = _pivot_high(index=2, confirmed_at=4, price=1005.0)
    candles = [_candle(i, 1000, 1001, 999, 1000) for i in range(10)]
    candles[5] = _candle(5, 1001.0, 1006.5, 1000.5, 1005.8)  # close > 1005.0

    mss = detect_mss(
        m5_candles=candles,
        m5_pivots=[pivot],
        sweep=_ssl_sweep(),
        sweep_m5_index=5,
        mss_timeout_bars=10,
        point=POINT,
    )
    assert mss is not None
    assert mss.direction == "BULLISH"
    assert mss.break_candle_index == 5
    assert mss.broken_pivot_price == 1005.0


def test_mss_timeout_expires():
    """No MSS within mss_timeout_bars → returns None."""
    pivot = _pivot_low(index=2, confirmed_at=4, price=995.0)
    # All candles close above 995.0
    candles = [_candle(i, 1000, 1001, 999, 1000) for i in range(20)]

    mss = detect_mss(
        m5_candles=candles,
        m5_pivots=[pivot],
        sweep=_bsl_sweep(),
        sweep_m5_index=5,
        mss_timeout_bars=5,   # only search idx 5-9
        point=POINT,
    )
    assert mss is None


def test_unconfirmed_pivot_not_used():
    """Pivot confirmed_at_index == break candle index → NOT available (strict less-than)."""
    # Pivot confirmed at index 5; searching from 5; strict < means idx 5 pivot NOT visible at idx 5
    pivot = _pivot_low(index=3, confirmed_at=5, price=995.0)
    candles = [_candle(i, 1000, 1001, 999, 1000) for i in range(10)]
    candles[5] = _candle(5, 999.0, 999.5, 994.0, 994.5)  # would break pivot if visible

    mss = detect_mss(
        m5_candles=candles,
        m5_pivots=[pivot],
        sweep=_bsl_sweep(),
        sweep_m5_index=5,
        mss_timeout_bars=10,
        point=POINT,
    )
    # Pivot is confirmed_at_index=5; at bar 5 we require confirmed_at_index < 5 → not visible
    # So no MSS at bar 5; at bar 6 the pivot IS visible (5 < 6)
    # candles[6] is flat at 1000 → close > 995, still no bearish MSS
    assert mss is None


def test_mss_records_bars_after_sweep():
    """bars_after_sweep counts M5 bars from sweep_m5_index to break_candle_index."""
    pivot = _pivot_low(index=2, confirmed_at=4, price=995.0)
    candles = [_candle(i, 1000, 1001, 999, 1000) for i in range(15)]
    # MSS happens 3 bars after sweep_m5_index=5, i.e. at index 8
    candles[8] = _candle(8, 999.0, 999.5, 994.0, 994.5)

    mss = detect_mss(
        m5_candles=candles,
        m5_pivots=[pivot],
        sweep=_bsl_sweep(),
        sweep_m5_index=5,
        mss_timeout_bars=10,
        point=POINT,
    )
    assert mss is not None
    assert mss.break_candle_index == 8
    assert mss.bars_after_sweep == 3   # 8 - 5 = 3
