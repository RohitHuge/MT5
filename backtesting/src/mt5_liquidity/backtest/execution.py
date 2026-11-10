"""Fill simulation: convert a signal into a fill price and TP/SL targets.

No MT5 imports. No order_send. Never called during live trading.

Bid/ask execution model (candle data is bid-based):
  BUY  → fill at ask = next_candle.open + (spread_points + slippage_points) * point
  SELL → fill at bid - slippage = next_candle.open - slippage_points * point

TP placement (from actual fill price):
  BUY  → tp = fill_price + abs(fill_price - sl_price) * rr_target
  SELL → tp = fill_price - abs(fill_price - sl_price) * rr_target

SELL exits use the ask side (see positions.py check_candle). Spread is therefore
paid once per round trip: at BUY entry and at SELL exit.

cost_note: "swap excluded (Phase 4)"
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class FillResult:
    setup_id: str
    direction: str              # "BUY" or "SELL"
    fill_price: float
    sl_price: float             # inherited from Setup.stop_loss_price
    tp_price: float
    lots: float
    fill_candle_index: int
    fill_candle_ts: datetime
    cost_note: str = "swap excluded (Phase 4)"


def compute_fill_price(
    direction: str,
    candle_open: float,
    spread_points: int,
    slippage_points: int,
    point: float,
) -> float:
    """
    Compute the executable fill price from a candle's open price.

    Used by the engine to size the position using the ACTUAL fill price
    before calling simulate_fill with the final lot count.
    """
    if direction == "BUY":
        return candle_open + (spread_points + slippage_points) * point
    else:  # SELL
        return candle_open - slippage_points * point


def simulate_fill(
    setup_id: str,
    direction: str,
    signal_candle_index: int,
    sl_price: float,
    lots: float,
    m5_candles: list,
    spread_points: int,
    slippage_points: int,
    rr_target: float,
    point: float,
) -> FillResult | None:
    """
    Simulate fill on the candle immediately after the signal candle.

    Returns None if there is no next candle (signal was on the last bar).

    Position sizing (lots) must already be calculated using compute_fill_price
    so that the monetary risk is based on the actual fill price, not the signal price.
    """
    fill_idx = signal_candle_index + 1
    if fill_idx >= len(m5_candles):
        return None

    next_candle = m5_candles[fill_idx]
    fill_price = compute_fill_price(direction, next_candle.open, spread_points, slippage_points, point)

    sl_distance = abs(fill_price - sl_price)
    if direction == "BUY":
        tp_price = fill_price + sl_distance * rr_target
    else:
        tp_price = fill_price - sl_distance * rr_target

    return FillResult(
        setup_id=setup_id,
        direction=direction,
        fill_price=round(fill_price, 10),
        sl_price=sl_price,
        tp_price=round(tp_price, 10),
        lots=lots,
        fill_candle_index=fill_idx,
        fill_candle_ts=next_candle.timestamp,
    )
