"""Open position tracking and bar-by-bar P&L resolution.

Historical candles contain bid OHLC. BUY exits are evaluated at bid; SELL exits
are evaluated at ask (bid + configured spread). Intrabar SL/TP ambiguity is
resolved conservatively by assuming SL first.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .execution import FillResult


class PositionStatus(Enum):
    OPEN = "OPEN"
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
    exit_reason: str
    fill_candle_index: int
    close_candle_index: int
    fill_ts: datetime
    close_ts: datetime
    bars_held: int
    value_per_point_per_lot: float
    point: float
    commission_usd: float = 0.0

    @property
    def net_pnl_usd(self) -> float:
        return round(self.pnl_usd - self.commission_usd, 2)


@dataclass
class OpenPosition:
    fill: FillResult
    value_per_point_per_lot: float
    point: float
    spread_points: int = 0
    exit_slippage_points: int = 0

    def _spread_price(self) -> float:
        return self.spread_points * self.point

    def _slippage_price(self) -> float:
        return self.exit_slippage_points * self.point

    def executable_exit_price(self, bid_price: float) -> float:
        """Return executable exit price including configured adverse slippage."""
        slip = self._slippage_price()
        if self.fill.direction == "BUY":
            return bid_price - slip
        return bid_price + self._spread_price() + slip

    def _pnl(self, close_price: float) -> float:
        if self.fill.direction == "BUY":
            price_diff = close_price - self.fill.fill_price
        else:
            price_diff = self.fill.fill_price - close_price
        return round((price_diff / self.point) * self.value_per_point_per_lot * self.fill.lots, 2)

    def check_candle(self, candle, candle_index: int) -> tuple[PositionStatus, float]:
        d = self.fill.direction
        sl, tp = self.fill.sl_price, self.fill.tp_price
        spread = self._spread_price()
        slip = self._slippage_price()

        if d == "BUY":
            raw_open, raw_high, raw_low = candle.open - slip, candle.high - slip, candle.low - slip
            gap_sl = raw_open <= sl
            hit_sl, hit_tp = raw_low <= sl, raw_high >= tp
            gap_close, sl_exit, tp_exit = raw_open, sl - slip, tp - slip
        else:
            raw_open = candle.open + spread + slip
            raw_high = candle.high + spread + slip
            raw_low = candle.low + spread + slip
            gap_sl = raw_open >= sl
            hit_sl, hit_tp = raw_high >= sl, raw_low <= tp
            gap_close, sl_exit, tp_exit = raw_open, sl + slip, tp + slip

        if gap_sl:
            return PositionStatus.HIT_SL, gap_close
        if hit_sl:  # Also covers the ambiguous both-hit case, conservatively.
            return PositionStatus.HIT_SL, sl_exit
        if hit_tp:
            return PositionStatus.HIT_TP, tp_exit
        return PositionStatus.OPEN, 0.0

    def check_entry_candle(self, candle) -> tuple[PositionStatus, float]:
        # The open is the fill event; check subsequent high/low for exits.
        d = self.fill.direction
        spread = self._spread_price()
        slip = self._slippage_price()
        if d == "BUY":
            low, high = candle.low - slip, candle.high - slip
            sl_exit, tp_exit = self.fill.sl_price - slip, self.fill.tp_price - slip
            hit_sl, hit_tp = low <= self.fill.sl_price, high >= self.fill.tp_price
        else:
            low, high = candle.low + spread + slip, candle.high + spread + slip
            sl_exit, tp_exit = self.fill.sl_price + slip, self.fill.tp_price + slip
            hit_sl, hit_tp = high >= self.fill.sl_price, low <= self.fill.tp_price
        if hit_sl:
            return PositionStatus.HIT_SL, sl_exit
        if hit_tp:
            return PositionStatus.HIT_TP, tp_exit
        return PositionStatus.OPEN, 0.0

    def unrealized_pnl(self, candle) -> float:
        return self._pnl(self.executable_exit_price(candle.close))

    def close(
        self, close_price: float, candle_index: int, candle_ts: datetime,
        exit_reason: str, commission_usd: float = 0.0,
    ) -> ClosedPosition:
        return ClosedPosition(
            setup_id=self.fill.setup_id, direction=self.fill.direction,
            fill_price=self.fill.fill_price, close_price=close_price,
            tp_price=self.fill.tp_price, sl_price=self.fill.sl_price,
            lots=self.fill.lots, pnl_usd=self._pnl(close_price),
            exit_reason=exit_reason, fill_candle_index=self.fill.fill_candle_index,
            close_candle_index=candle_index, fill_ts=self.fill.fill_candle_ts,
            close_ts=candle_ts, bars_held=candle_index - self.fill.fill_candle_index,
            value_per_point_per_lot=self.value_per_point_per_lot, point=self.point,
            commission_usd=commission_usd,
        )
