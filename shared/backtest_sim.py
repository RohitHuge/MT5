"""SL/TP fill simulation shared by the strategy services' /backtest endpoints.

The signal detection itself is NOT duplicated here — each strategy service
passes in its own signals, produced by the exact same function the live path
uses. This module only walks forward over the candles and decides whether the
trade hit its take profit or stop loss.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol


class CandleLike(Protocol):
    timestamp: datetime
    high: float
    low: float
    close: float


def _pnl(entry: float, exit_price: float, direction: str, lots: float, multiplier: float) -> float:
    sign = 1.0 if direction.upper() == "BUY" else -1.0
    return (exit_price - entry) * sign * lots * multiplier


def simulate_trades(
    candles: list[CandleLike],
    signals: list[dict[str, Any]],
    point: float = 0.01,
    tick_value: float = 1.0,
    commission_per_lot: float = 0.0,
) -> list[dict[str, Any]]:
    """Resolve each signal against subsequent candles.

    Each entry in *signals* needs: direction, entry_price, sl_price, tp_price,
    lots, signal_ts and index (the bar the signal fired on). A trade opens at
    the signal bar's close and is resolved on a later bar.

    When a bar's range covers both TP and SL, the SL is assumed to fill first —
    the conservative reading, since intrabar ordering is unknowable.

    Trades still open at the end of the data are closed at the last candle with
    outcome "FORCE_CLOSE", so every returned trade carries a realised P&L.
    """
    multiplier = tick_value / point if point else 1.0
    out: list[dict[str, Any]] = []
    last_i = len(candles) - 1

    for sig in signals:
        direction = str(sig["direction"]).upper()
        entry = float(sig["entry_price"])
        sl = float(sig["sl_price"])
        tp = float(sig["tp_price"])
        lots = float(sig.get("lots") or 0.0)
        start = int(sig.get("index") or 0)
        signal_ts = sig.get("signal_ts")
        commission = round(lots * commission_per_lot, 2)

        close_price: float | None = None
        outcome: str | None = None
        closed_at: datetime | None = None

        for i in range(start + 1, len(candles)):
            bar = candles[i]
            if direction == "BUY":
                hit_sl = bar.low <= sl
                hit_tp = bar.high >= tp
            else:
                hit_sl = bar.high >= sl
                hit_tp = bar.low <= tp

            if hit_sl:
                close_price, outcome = sl, "SL"
            elif hit_tp:
                close_price, outcome = tp, "TP"
            if outcome is not None:
                closed_at = bar.timestamp
                break

        if outcome is None:
            if last_i >= 0:
                close_price = candles[last_i].close
                closed_at = candles[last_i].timestamp
                outcome = "FORCE_CLOSE"
            else:
                continue

        gross = round(_pnl(entry, close_price, direction, lots, multiplier), 2)
        net = round(gross - commission, 2)

        out.append(
            {
                "direction": direction,
                "entry_price": round(entry, 2),
                "sl_price": round(sl, 2),
                "tp_price": round(tp, 2),
                "lots": lots,
                "close_price": round(close_price, 2),
                "outcome": outcome,
                "gross_pnl": gross,
                "commission": commission,
                "net_pnl": net,
                "opened_at": signal_ts.isoformat() if hasattr(signal_ts, "isoformat") else signal_ts,
                "closed_at": closed_at.isoformat() if closed_at else None,
            }
        )

    return out
