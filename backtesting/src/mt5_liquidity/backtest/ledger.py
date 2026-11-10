"""Trade ledger: collect closed positions and compute summary statistics.

No MT5 imports. No order_send.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .positions import ClosedPosition


@dataclass
class LedgerStats:
    total_trades: int
    winners: int
    losers: int
    win_rate: float             # 0.0–1.0
    gross_profit: float         # sum of positive pnl_usd
    gross_loss: float           # sum of negative pnl_usd (negative value)
    net_pnl: float
    profit_factor: float        # gross_profit / abs(gross_loss); inf if no losses
    avg_win: float
    avg_loss: float             # negative
    max_drawdown_usd: float     # peak-to-trough on equity curve (positive = drawdown size)
    max_drawdown_pct: float     # as % of equity at peak
    final_equity: float


@dataclass
class TradeLedger:
    initial_equity: float
    trades: list[ClosedPosition] = field(default_factory=list)

    def record(self, trade: ClosedPosition) -> None:
        self.trades.append(trade)

    def compute_stats(self) -> LedgerStats:
        trades = self.trades
        total = len(trades)
        if total == 0:
            return LedgerStats(
                total_trades=0, winners=0, losers=0,
                win_rate=0.0, gross_profit=0.0, gross_loss=0.0, net_pnl=0.0,
                profit_factor=0.0, avg_win=0.0, avg_loss=0.0,
                max_drawdown_usd=0.0, max_drawdown_pct=0.0,
                final_equity=self.initial_equity,
            )

        wins = [t for t in trades if t.pnl_usd > 0]
        losses = [t for t in trades if t.pnl_usd <= 0]

        gross_profit = sum(t.pnl_usd for t in wins)
        gross_loss   = sum(t.pnl_usd for t in losses)  # negative or 0
        net_pnl      = gross_profit + gross_loss

        profit_factor = (
            gross_profit / abs(gross_loss)
            if gross_loss != 0
            else float("inf")
        )

        avg_win  = gross_profit / len(wins)  if wins   else 0.0
        avg_loss = gross_loss   / len(losses) if losses else 0.0

        # Equity curve peak-to-trough drawdown
        equity = self.initial_equity
        peak = equity
        max_dd_usd = 0.0
        max_dd_pct = 0.0
        for t in trades:
            equity += t.pnl_usd
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd_usd:
                max_dd_usd = dd
                max_dd_pct = dd / peak * 100.0 if peak > 0 else 0.0

        return LedgerStats(
            total_trades=total,
            winners=len(wins),
            losers=len(losses),
            win_rate=len(wins) / total,
            gross_profit=round(gross_profit, 2),
            gross_loss=round(gross_loss, 2),
            net_pnl=round(net_pnl, 2),
            profit_factor=round(profit_factor, 4) if profit_factor != float("inf") else profit_factor,
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            max_drawdown_usd=round(max_dd_usd, 2),
            max_drawdown_pct=round(max_dd_pct, 4),
            final_equity=round(self.initial_equity + net_pnl, 2),
        )
