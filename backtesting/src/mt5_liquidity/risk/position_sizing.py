"""Position sizing from risk percentage. No MT5 imports, no order_send."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class SizingResult:
    lots: float
    risk_amount: float          # USD
    sl_points: float
    value_per_point_per_lot: float
    rejection_reason: str | None  # None → accepted; set → rejected


def calculate_position_size(
    signal_price: float,
    stop_loss_price: float,
    equity: float,
    risk_pct: float,
    volume_min: float,
    volume_max: float,
    volume_step: float,
    tick_value: float,
    tick_size: float,
    point: float,
    min_stop_distance_points: float = 0.0,
    max_lots_per_trade: float | None = None,
) -> SizingResult:
    """
    Calculate lots from risk percentage.

    if min_stop_distance_points < 0:
        return SizingResult(0.0, 0.0, sl_points, 0.0, "min_stop_distance_points cannot be negative")
    if sl_points < min_stop_distance_points:
        value = (tick_value / tick_size) * point
        return SizingResult(0.0, equity * risk_pct / 100.0, sl_points, value,
                            f"SL distance {sl_points:.2f} points < minimum {min_stop_distance_points:.2f} points")
    if max_lots_per_trade is not None and max_lots_per_trade <= 0:
        return SizingResult(0.0, equity * risk_pct / 100.0, sl_points, 0.0,
                            "max_lots_per_trade must be > 0")

    value_per_point_per_lot = (tick_value / tick_size) * point
      For XAUUSD: tick_value=1.00, tick_size=0.01, point=0.01 → 1.00 $/point/lot

    sl_points = abs(signal_price - stop_loss_price) / point
    risk_amount = equity * risk_pct / 100
    lots_raw = risk_amount / (sl_points * value_per_point_per_lot)
    lots = floor(lots_raw / volume_step) * volume_step  (round DOWN)

    Returns SizingResult with rejection_reason set when lots < volume_min.
    """
    if point <= 0 or tick_size <= 0 or tick_value <= 0 or volume_step <= 0:
        return SizingResult(0.0, 0.0, 0.0, 0.0, "Invalid symbol sizing metadata")

    if sl_points_distance := abs(signal_price - stop_loss_price):
        sl_points = sl_points_distance / point
    else:
        return SizingResult(
            lots=0.0,
            risk_amount=0.0,
            sl_points=0.0,
            value_per_point_per_lot=0.0,
            rejection_reason="SL distance is zero — cannot size position",
        )

    value_per_point_per_lot = (tick_value / tick_size) * point
    risk_amount = equity * risk_pct / 100.0
    risk_per_lot = sl_points * value_per_point_per_lot

    if risk_per_lot <= 0:
        return SizingResult(
            lots=0.0,
            risk_amount=risk_amount,
            sl_points=sl_points,
            value_per_point_per_lot=value_per_point_per_lot,
            rejection_reason="risk_per_lot <= 0",
        )

    lots_raw = risk_amount / risk_per_lot
    # Round DOWN to nearest volume_step (never risk more than requested)
    steps = math.floor(lots_raw / volume_step)
    lots = round(steps * volume_step, 10)

    if lots < volume_min:
        return SizingResult(
            lots=0.0,
            risk_amount=risk_amount,
            sl_points=sl_points,
            value_per_point_per_lot=value_per_point_per_lot,
            rejection_reason=f"lots {lots:.5f} < volume_min {volume_min:.5f}",
        )

    effective_max = min(volume_max, max_lots_per_trade) if max_lots_per_trade is not None else volume_max
    if lots > effective_max:
        lots = math.floor(effective_max / volume_step) * volume_step
        lots = round(lots, 10)
    if lots < volume_min:
        return SizingResult(0.0, risk_amount, sl_points, value_per_point_per_lot,
                            f"capped lots {lots:.5f} < volume_min {volume_min:.5f}")

    return SizingResult(
        lots=lots,
        risk_amount=risk_amount,
        sl_points=sl_points,
        value_per_point_per_lot=value_per_point_per_lot,
        rejection_reason=None,
    )
