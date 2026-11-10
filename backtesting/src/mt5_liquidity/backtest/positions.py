"""Open position tracking and bar-by-bar P&L resolution.

Conservative ambiguity rule: when a candle hits both SL and TP in the same bar,
SL is assumed to have been hit first (worst-case outcome).

Gap rule: if the candle opens beyond the SL (gap through), exit is at candle.open,
not the SL price — the market gapped and cannot guarantee the SL fill.

P&L formula:
  pnl_usd = (price_diff / point) * value_per_point_per_lot * lots
  where value_per_point_per_lot = (tick_value / tick_size) * point
  For XAUUSD: (1.0 / 0.01) * 0.01 = 1.0 $/point/lot

No MT5 imports. No order_send.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .execution import FillResult


class PositionStatus(Enum):
    OPEN   = "OPEN"
    HIT_TP = "HIT_TP"
    HIT_SL = "HIT_SL"


@dataclass
class ClosedPosition:
    setup_id: str
    direction: str
    fill_price: float
    close_price: float
    tp_price: float
    sl_price: float
    lots: float
    pnl_usd: float
    exit_reason: str            # "TP", "SL", "FORCE_CLOSE"
    fill_candle_index: int
    close_candle_index: int
    fill_ts: datetime
    close_ts: datetime
    bars_held: int
    value_per_point_per_lot: float
    point: float


@dataclass
class OpenPosition:
    fill: FillResult
    value_per_point_per_lot: float
    point: float

    def _pnl(self, close_price: float) -> float:
        """Calculate P&L in USD. BUY profits when price rises; SELL when it falls."""
        if self.fill.direction == "BUY":
            price_diff = close_price - self.fill.fill_price
        else:
            price_diff = self.fill.fill_price - close_price
        return round((price_diff / self.point) * self.value_per_point_per_lot * self.fill.lots, 2)

    def check_candle(self, candle, candle_index: int) -> tuple[PositionStatus, float]:
        """
        Inspect a single bar and return (status, close_price).

        close_price is 0.0 when the position remains open (caller ignores it).
        """
        d = self.fill.direction
        sl = self.fill.sl_price
        tp = self.fill.tp_price

        if d == "BUY":
            gap_through_sl = candle.open <= sl
            hit_sl = candle.low <= sl
            hit_tp = candle.high >= tp
        else:
            gap_through_sl = candle.open >= sl
            hit_sl = candle.high >= sl
            hit_tp = candle.low <= tp

        if gap_through_sl:
            return PositionStatus.HIT_SL, candle.open

        if hit_sl and hit_tp:
            # Ambiguous bar: conservative — SL hit first
            return PositionStatus.HIT_SL, sl

        if hit_sl:
            return PositionStatus.HIT_SL, sl

        if hit_tp:
            return PositionStatus.HIT_TP, tp

        return PositionStatus.OPEN, 0.0

    def close(
        self,
        close_price: float,
        candle_index: int,
        candle_ts: datetime,
        exit_reason: str,
    ) -> ClosedPosition:
        return ClosedPosition(
            setup_id=self.fill.setup_id,
            direction=self.fill.direction,
            fill_price=self.fill.fill_price,
            close_price=close_price,
            tp_price=self.fill.tp_price,
            sl_price=self.fill.sl_price,
            lots=self.fill.lots,
            pnl_usd=self._pnl(close_price),
            exit_reason=exit_reason,
            fill_candle_index=self.fill.fill_candle_index,
            close_candle_index=candle_index,
            fill_ts=self.fill.fill_candle_ts,
            close_ts=candle_ts,
            bars_held=candle_index - self.fill.fill_candle_index,
            value_per_point_per_lot=self.value_per_point_per_lot,
            point=self.point,
        )
