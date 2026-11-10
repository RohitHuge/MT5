"""High-level history retrieval — normalises MT5 raw rates into Candle objects."""
from __future__ import annotations

import logging
from datetime import datetime

from .candles import AggregationResult, Candle, aggregate_m5_to_m15
from .mt5_adapter import MT5Adapter

logger = logging.getLogger(__name__)


def _rates_to_candles(rates: list[dict], timeframe: str) -> list[Candle]:
    return [
        Candle(
            timestamp=r["timestamp"],
            open=r["open"],
            high=r["high"],
            low=r["low"],
            close=r["close"],
            volume=r["volume"],
            spread=r["spread"],
            timeframe=timeframe,
        )
        for r in rates
    ]


def fetch_m5(
    adapter: MT5Adapter,
    symbol: str,
    from_dt: datetime,
    to_dt: datetime,
) -> list[Candle]:
    """Fetch M5 candles and return as Candle objects with UTC timestamps."""
    import MetaTrader5 as mt5
    rates = adapter.get_rates(symbol, mt5.TIMEFRAME_M5, from_dt, to_dt)
    return _rates_to_candles(rates, "M5")


def fetch_m15_native(
    adapter: MT5Adapter,
    symbol: str,
    from_dt: datetime,
    to_dt: datetime,
) -> list[Candle]:
    """Fetch native M15 candles from MT5 for comparison against derived M15."""
    import MetaTrader5 as mt5
    rates = adapter.get_rates(symbol, mt5.TIMEFRAME_M15, from_dt, to_dt)
    return _rates_to_candles(rates, "M15")


def compare_m15(derived: list[Candle] | AggregationResult, native: list[Candle]) -> dict:
    """
    Compare derived and native M15 candles in both directions.
    Reports missing timestamps on either side as well as OHLC mismatches.
    Coverage is matched / union-of-timestamps, preventing omitted candles
    from being hidden by a derived-only denominator.
    """
    if isinstance(derived, AggregationResult):
        derived = derived.candles

    derived_map = {c.timestamp: c for c in derived}
    native_map = {c.timestamp: c for c in native}
    matched = 0
    mismatched: list[dict] = []
    missing_derived: list[str] = []
    missing_native: list[str] = []

    for ts in sorted(set(derived_map) | set(native_map)):
        d = derived_map.get(ts)
        n = native_map.get(ts)
        if d is None:
            missing_derived.append(ts.isoformat())
            mismatched.append({
                "ts": ts.isoformat(),
                "derived_ohlc": None,
                "native_ohlc": (n.open, n.high, n.low, n.close),
                "reason": "missing derived candle",
            })
            continue
        if n is None:
            missing_native.append(ts.isoformat())
            mismatched.append({
                "ts": ts.isoformat(),
                "derived_ohlc": (d.open, d.high, d.low, d.close),
                "native_ohlc": None,
                "reason": "missing native candle",
            })
            continue

        tol = 1e-6
        if (
            abs(d.open - n.open) > tol
            or abs(d.high - n.high) > tol
            or abs(d.low - n.low) > tol
            or abs(d.close - n.close) > tol
        ):
            mismatched.append({
                "ts": ts.isoformat(),
                "derived_ohlc": (d.open, d.high, d.low, d.close),
                "native_ohlc": (n.open, n.high, n.low, n.close),
                "reason": "OHLC mismatch",
            })
        else:
            matched += 1

    total = len(set(derived_map) | set(native_map))
    coverage_pct = round(matched / total * 100, 2) if total else 0.0
    logger.info(
        "M15 comparison: %d matched, %d mismatched, %d missing-derived, "
        "%d missing-native, %.1f%% coverage",
        matched, len(mismatched), len(missing_derived), len(missing_native), coverage_pct,
    )
    return {
        "matched": matched,
        "mismatched": mismatched,
        "missing_derived": missing_derived,
        "missing_native": missing_native,
        "coverage_pct": coverage_pct,
    }
