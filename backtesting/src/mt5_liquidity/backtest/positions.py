"""Open position tracking and bar-by-bar P&L resolution.

Bid/ask execution model (candle OHLC data is bid-based):

BUY positions:
  Entry  : ask = bid_open + (spread + slippage) * point
  Exit   : bid  (SL/TP levels evaluated against bid prices)
  P&L    : bid_exit - ask_entry

SELL positions:
  Entry  : bid = bid_open - slippage * point
  Exit   : ask = bid_exit + spread * point
  SL/TP  : triggered when ask-side price reaches the level
           → hit_sl:  bid_high + spread >= sl_price  → exit at sl_price (ask = sl)
           → hit_tp:  bid_low  + spread <= tp_price  → exit at tp_price (ask = tp)
           → gap_sl:  bid_open + spread >= sl_price  → exit at bid_open + spread (ask open)
  P&L    : bid_entry - ask_exit   (spread paid at exit, once per round trip)

Conservative ambiguity rule: when both SL and TP are touched in the same bar,
SL is assumed to have been hit first (worst-case outcome).

check_entry_candle: evaluates the fill candle for immediate SL/TP. The gap check
is omitted because we just filled at the candle open — there is no prior gap from
our perspective. SL/TP is evaluated against the full candle range from open onward.

No MT5 imports. No order_send.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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
    pnl_usd: float              # gross P&L before commission
    exit_reason: str            # "TP", "SL", "FORCE_CLOSE"
    fill_candle_index: int
    close_candle_index: int
    fill_ts: datetime
    close_ts: datetime
    bars_held: int
    value_per_point_per_lot: float
    point: float
    commission_usd: float = 0.0  # total round-trip commission (entry + exit)

    @property
    def net_pnl_usd(self) -> float:
        """P&L after all commissions."""
        return round(self.pnl_usd - self.commission_usd, 2)


@dataclass
class OpenPosition:
    fill: FillResult
    value_per_point_per_lot: float
    point: float
    spread_points: int = 0      # used for SELL ask-side exit evaluation

    def _spread_price(self) -> float:
        return self.spread_points * self.point

    def _pnl(self, close_price: float) -> float:
        """
        Gross P&L in USD.

        close_price must be in the correct side for the direction:
          BUY  → close_price is bid
          SELL → close_price is ask (bid + spread already added by caller)
        """
        if self.fill.direction == "BUY":
            price_diff = close_price - self.fill.fill_price
        else:
            price_diff = self.fill.fill_price - close_price
        return round((price_diff / self.point) * self.value_per_point_per_lot * self.fill.lots, 2)

    def check_candle(self, candle, candle_index: int) -> tuple[PositionStatus, float]:
        """
        Inspect a single bar and return (status, close_price).

        close_price is 0.0 when OPEN (caller ignores it).

        For SELL positions the check is performed on the ask-side price so that
        spread is correctly paid at exit (not double-counted from entry).
        """
        d = self.fill.direction
        sl = self.fill.sl_price
        tp = self.fill.tp_price
        spread = self._spread_price()

        if d == "BUY":
            gap_through_sl = candle.open <= sl
            hit_sl = candle.low <= sl
            hit_tp = candle.high >= tp
            gap_close = candle.open
            sl_exit  = sl
            tp_exit  = tp
        else:
            # SELL exits at ask = bid + spread
            # Trigger when ask-side price reaches the level
            gap_through_sl = (candle.open + spread) >= sl
            hit_sl = (candle.high + spread) >= sl
            hit_tp = (candle.low  + spread) <= tp
            gap_close = candle.open + spread   # actual ask open
            sl_exit   = sl                     # ask = sl when bid = sl - spread
            tp_exit   = tp                     # ask = tp when bid = tp - spread

        if gap_through_sl:
            return PositionStatus.HIT_SL, gap_close

        if hit_sl and hit_tp:
            return PositionStatus.HIT_SL, sl_exit

        if hit_sl:
            return PositionStatus.HIT_SL, sl_exit

        if hit_tp:
            return PositionStatus.HIT_TP, tp_exit

        return PositionStatus.OPEN, 0.0

    def check_entry_candle(self, candle) -> tuple[PositionStatus, float]:
        """
        Evaluate SL/TP on the fill candle itself.

        The gap check is skipped: we just opened at the candle's open price, so
        there is no prior gap from our perspective. SL/TP is evaluated against
        the full price range of the fill candle. Spread model is the same as
        check_candle.
        """
        d = self.fill.direction
        sl = self.fill.sl_price
        tp = self.fill.tp_price
        spread = self._spread_price()

        if d == "BUY":
            hit_sl = candle.low  <= sl
            hit_tp = candle.high >= tp
            sl_exit = sl
            tp_exit = tp
        else:
            hit_sl = (candle.high + spread) >= sl
            hit_tp = (candle.low  + spread) <= tp
            sl_exit = sl
            tp_exit = tp

        if hit_sl and hit_tp:
            return PositionStatus.HIT_SL, sl_exit
        if hit_sl:
            return PositionStatus.HIT_SL, sl_exit
        if hit_tp:
            return PositionStatus.HIT_TP, tp_exit
        return PositionStatus.OPEN, 0.0

    def unrealized_pnl(self, candle) -> float:
        """
        Mark-to-market estimate using candle close.

        BUY : marked at bid close.
        SELL: marked at ask close (bid + spread) so the spread cost is visible
              in the daily equity calculation.
        """
        if self.fill.direction == "BUY":
            mark = candle.close
        else:
            mark = candle.close + self._spread_price()
        return self._pnl(mark)

    def close(
        self,
        close_price: float,
        candle_index: int,
        candle_ts: datetime,
        exit_reason: str,
        commission_usd: float = 0.0,
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
            commission_usd=commission_usd,
        )
