"""Trade ledger: collect closed positions and compute summary statistics.

No MT5 imports. No order_send.

Terminology:
  gross_profit / gross_loss : P&L excluding commission
  total_commission          : all round-trip commissions summed
  net_pnl                   : gross_profit + gross_loss - total_commission
  final_equity              : initial_equity + net_pnl

Winners/losers are classified by net P&L (after commission) so that a small gross
win erased by commission is correctly counted as a loser.

Swap is excluded and noted in cost_note.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .positions import ClosedPosition


@dataclass
class LedgerStats:
    total_trades: int
    winners: int
    losers: int
    win_rate: float              # 0.0–1.0, based on net P&L
    gross_profit: float          # sum of positive gross pnl_usd
    gross_loss: float            # sum of negative gross pnl_usd (negative value)
    total_commission: float      # sum of all commission_usd across all trades
    net_pnl: float               # gross_profit + gross_loss - total_commission
    profit_factor: float         # gross_profit / abs(gross_loss); inf if no losses
    avg_win: float               # average net P&L of winning trades
    avg_loss: float              # average net P&L of losing trades (negative)
    max_drawdown_usd: float      # peak-to-trough on equity curve
    max_drawdown_pct: float      # as % of equity at peak
    final_equity: float
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    cost_note: str = "swap excluded (Phase 4)"


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
                win_rate=0.0, gross_profit=0.0, gross_loss=0.0,
                total_commission=0.0, net_pnl=0.0,
                profit_factor=0.0, avg_win=0.0, avg_loss=0.0,
                max_drawdown_usd=0.0, max_drawdown_pct=0.0,
                final_equity=self.initial_equity,
            )

        # Gross P&L (before commission) used for gross_profit / gross_loss / profit_factor
        gross_profit = sum(t.pnl_usd for t in trades if t.pnl_usd > 0)
        gross_loss   = sum(t.pnl_usd for t in trades if t.pnl_usd <= 0)
        total_commission = sum(t.commission_usd for t in trades)
        net_pnl = gross_profit + gross_loss - total_commission

        profit_factor = (
            gross_profit / abs(gross_loss)
            if gross_loss != 0
            else float("inf")
        )

        # Winners/losers classified by net P&L
        wins   = [t for t in trades if t.net_pnl_usd > 0]
        losses = [t for t in trades if t.net_pnl_usd <= 0]

        avg_win  = sum(t.net_pnl_usd for t in wins)   / len(wins)   if wins   else 0.0
        avg_loss = sum(t.net_pnl_usd for t in losses) / len(losses) if losses else 0.0

        # Peak-to-trough drawdown on cumulative equity (using net P&L per trade)
        equity = self.initial_equity
        peak = equity
        max_dd_usd = 0.0
        max_dd_pct = 0.0
        for t in trades:
            equity += t.net_pnl_usd
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd_usd:
                max_dd_usd = dd
                max_dd_pct = dd / peak * 100.0 if peak > 0 else 0.0

        # Consecutive wins/losses
        max_cons_wins = max_cons_losses = 0
        cur_wins = cur_losses = 0
        for t in trades:
            if t.net_pnl_usd > 0:
                cur_wins += 1
                cur_losses = 0
            else:
                cur_losses += 1
                cur_wins = 0
            max_cons_wins   = max(max_cons_wins,   cur_wins)
            max_cons_losses = max(max_cons_losses, cur_losses)

        return LedgerStats(
            total_trades=total,
            winners=len(wins),
            losers=len(losses),
            win_rate=len(wins) / total,
            gross_profit=round(gross_profit, 2),
            gross_loss=round(gross_loss, 2),
            total_commission=round(total_commission, 2),
            net_pnl=round(net_pnl, 2),
            profit_factor=round(profit_factor, 4) if profit_factor != float("inf") else profit_factor,
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            max_drawdown_usd=round(max_dd_usd, 2),
            max_drawdown_pct=round(max_dd_pct, 4),
            final_equity=round(self.initial_equity + net_pnl, 2),
            max_consecutive_wins=max_cons_wins,
            max_consecutive_losses=max_cons_losses,
        )
