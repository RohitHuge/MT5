"""Synthetic candle series for deterministic unit testing."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mt5_liquidity.data.candles import Candle

BASE_TIME = datetime(2025, 1, 6, 0, 0, tzinfo=timezone.utc)  # Monday
M5 = timedelta(minutes=5)


def _ts(start: datetime | None, i: int) -> datetime:
    base = start if start is not None else BASE_TIME
    return base + i * M5


def make_candle(
    ts: datetime,
    o: float,
    h: float,
    l: float,
    c: float,
    tf: str = "M5",
) -> Candle:
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, timeframe=tf)


def flat_series(
    n: int,
    price: float = 1000.0,
    start: datetime | None = None,
) -> list[Candle]:
    """n candles all with the same OHLC."""
    return [make_candle(_ts(start, i), price, price, price, price) for i in range(n)]


def trending_up(
    n: int,
    start_price: float = 1000.0,
    step: float = 1.0,
    start: datetime | None = None,
) -> list[Candle]:
    """Each candle: o=prev_close, h=o+step, l=o, c=o+step."""
    candles: list[Candle] = []
    price = start_price
    for i in range(n):
        o = price
        h = o + step
        l = o
        c = h
        candles.append(make_candle(_ts(start, i), o, h, l, c))
        price = c
    return candles


def trending_down(
    n: int,
    start_price: float = 1000.0,
    step: float = 1.0,
    start: datetime | None = None,
) -> list[Candle]:
    """Each candle: o=prev_close, h=o, l=o-step, c=o-step."""
    candles: list[Candle] = []
    price = start_price
    for i in range(n):
        o = price
        h = o
        l = o - step
        c = l
        candles.append(make_candle(_ts(start, i), o, h, l, c))
        price = c
    return candles


def with_swing_high(
    base_price: float,
    swing_price: float,
    swing_at: int,
    n: int,
    start: datetime | None = None,
) -> list[Candle]:
    """
    n candles flat at base_price, with one spike high at index swing_at.
    Swing candle: H=swing_price, O=C=L=base_price.
    """
    candles: list[Candle] = []
    for i in range(n):
        if i == swing_at:
            candles.append(make_candle(_ts(start, i), base_price, swing_price, base_price, base_price))
        else:
            candles.append(make_candle(_ts(start, i), base_price, base_price, base_price, base_price))
    return candles


def with_swing_low(
    base_price: float,
    swing_price: float,
    swing_at: int,
    n: int,
    start: datetime | None = None,
) -> list[Candle]:
    """
    n candles flat at base_price, with one spike low at index swing_at.
    Swing candle: L=swing_price, O=C=H=base_price.
    """
    candles: list[Candle] = []
    for i in range(n):
        if i == swing_at:
            candles.append(make_candle(_ts(start, i), base_price, base_price, swing_price, base_price))
        else:
            candles.append(make_candle(_ts(start, i), base_price, base_price, base_price, base_price))
    return candles


def zigzag_series(
    n: int,
    base: float = 1000.0,
    amplitude: float = 5.0,
    start: datetime | None = None,
) -> list[Candle]:
    """
    Alternating up/down candles producing clear swing highs and lows.
    Even indices: up candle peaking at base+amplitude.
    Odd indices: down candle troughing at base-amplitude.
    """
    candles: list[Candle] = []
    for i in range(n):
        if i % 2 == 0:
            candles.append(make_candle(_ts(start, i), base, base + amplitude, base, base + amplitude))
        else:
            candles.append(make_candle(_ts(start, i), base, base, base - amplitude, base - amplitude))
    return candles
