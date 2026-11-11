"""Trade ledger: collect closed positions and compute summary statistics.

Profit factor is gross (before commission); net P&L and win/loss classification
include commission. Swap remains excluded unless explicitly modelled later.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from .positions import ClosedPosition


@dataclass
class LedgerStats:
    total_trades: int
    winners: int
    losers: int
    win_rate: float
    gross_profit: float
    gross_loss: float
    total_commission: float
    net_pnl: float
    profit_factor: float  # gross profit / abs(gross loss), before commission
    avg_win: float
    avg_loss: float
    max_drawdown_usd: float
    max_drawdown_pct: float
    final_equity: float
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    cost_note: str = "swap excluded; profit factor is gross (pre-commission)"


@dataclass
class TradeLedger:
    initial_equity: float
    trades: list[ClosedPosition] = field(default_factory=list)

    def record(self, trade: ClosedPosition) -> None:
        self.trades.append(trade)

    def compute_stats(self) -> LedgerStats:
        trades = self.trades
        total = len(trades)
        if not total:
            return LedgerStats(
                total_trades=0, winners=0, losers=0, win_rate=0.0,
                gross_profit=0.0, gross_loss=0.0, total_commission=0.0,
                net_pnl=0.0, profit_factor=0.0, avg_win=0.0, avg_loss=0.0,
                max_drawdown_usd=0.0, max_drawdown_pct=0.0,
                final_equity=self.initial_equity,
            )

        gross_profit = sum(t.pnl_usd for t in trades if t.pnl_usd > 0)
        gross_loss = sum(t.pnl_usd for t in trades if t.pnl_usd <= 0)
        commission = sum(t.commission_usd for t in trades)
        net_pnl = gross_profit + gross_loss - commission
        gross_pf = gross_profit / abs(gross_loss) if gross_loss else float("inf")

        wins = [t for t in trades if t.net_pnl_usd > 0]
        losses = [t for t in trades if t.net_pnl_usd <= 0]
        avg_win = sum(t.net_pnl_usd for t in wins) / len(wins) if wins else 0.0
        avg_loss = sum(t.net_pnl_usd for t in losses) / len(losses) if losses else 0.0

        equity = peak = self.initial_equity
        max_dd_usd = max_dd_pct = 0.0
        for trade in trades:
            equity += trade.net_pnl_usd
            peak = max(peak, equity)
            drawdown = peak - equity
            if drawdown > max_dd_usd:
                max_dd_usd = drawdown
                max_dd_pct = drawdown / peak * 100.0 if peak > 0 else 0.0

        max_wins = max_losses = current_wins = current_losses = 0
        for trade in trades:
            if trade.net_pnl_usd > 0:
                current_wins += 1
                current_losses = 0
            else:
                current_losses += 1
                current_wins = 0
            max_wins = max(max_wins, current_wins)
            max_losses = max(max_losses, current_losses)

        return LedgerStats(
            total_trades=total, winners=len(wins), losers=len(losses),
            win_rate=len(wins) / total, gross_profit=round(gross_profit, 2),
            gross_loss=round(gross_loss, 2), total_commission=round(commission, 2),
            net_pnl=round(net_pnl, 2),
            profit_factor=round(gross_pf, 4) if gross_pf != float("inf") else gross_pf,
            avg_win=round(avg_win, 2), avg_loss=round(avg_loss, 2),
            max_drawdown_usd=round(max_dd_usd, 2), max_drawdown_pct=round(max_dd_pct, 4),
            final_equity=round(self.initial_equity + net_pnl, 2),
            max_consecutive_wins=max_wins, max_consecutive_losses=max_losses,
        )
