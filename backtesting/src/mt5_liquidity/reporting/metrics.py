"""Console reporting for backtest results. Only this layer uses print()."""
from __future__ import annotations

from ..backtest.engine import BacktestResult
from ..backtest.ledger import LedgerStats
from . import console

_DISCLAIMER = (
    "** SIMULATED RESULTS — historical performance does not guarantee future profitability **"
)


def print_backtest_report(result: BacktestResult, symbol: str, from_date: str, to_date: str) -> None:
    console.print_header(f"BACKTEST REPORT  {symbol}  [{from_date} → {to_date}]")
    print(f"  {_DISCLAIMER}")
    print()

    print(f"  Setups found      : {result.setups_total}")
    print(f"  ENTRY_READY       : {result.setups_entry_ready}")
    print(f"  Trades opened     : {result.trades_opened}")
    print(f"  Force-closed      : {result.force_closed}")
    print()

    print(f"  Skips — max pos   : {result.setups_skipped_max_pos}")
    print(f"  Skips — daily lim : {result.setups_skipped_daily_loss}")
    print(f"  Skips — sizing    : {result.setups_skipped_sizing}")
    print(f"  Skips — no bar    : {result.setups_skipped_no_next_bar}")
    print()

    _print_stats(result.stats)

    if result.closed_positions:
        print("  Trade log:")
        print(f"  {'Setup ID':<30} {'Dir':<5} {'Fill':>8} {'Close':>8} {'Gross':>8} {'Comm':>7} {'Net':>8} {'Exit':<12}")
        print("  " + "-" * 92)
        for t in result.closed_positions:
            print(
                f"  {t.setup_id:<30} {t.direction:<5} {t.fill_price:>8.2f} "
                f"{t.close_price:>8.2f} {t.pnl_usd:>+8.2f} {t.commission_usd:>7.2f} "
                f"{t.net_pnl_usd:>+8.2f} {t.exit_reason:<12}"
            )
    print()


def _print_stats(s: LedgerStats) -> None:
    console.print_header("STATISTICS")
    print(f"  Total trades      : {s.total_trades}")
    print(f"  Winners / Losers  : {s.winners} / {s.losers}")
    win_pct = f"{s.win_rate * 100:.1f}%" if s.total_trades else "n/a"
    print(f"  Win rate          : {win_pct}")
    print(f"  Consec wins/loss  : {s.max_consecutive_wins} / {s.max_consecutive_losses}")
    print()
    print(f"  Gross profit      : ${s.gross_profit:,.2f}")
    print(f"  Gross loss        : ${s.gross_loss:,.2f}")
    print(f"  Commission (total): ${s.total_commission:,.2f}")
    print(f"  Swap              : {s.cost_note}")
    print(f"  Net P&L           : ${s.net_pnl:+,.2f}")
    pf = f"{s.profit_factor:.4f}" if s.profit_factor != float("inf") else "inf"
    print(f"  Profit factor     : {pf}")
    print(f"  Avg win (net)     : ${s.avg_win:,.2f}")
    print(f"  Avg loss (net)    : ${s.avg_loss:,.2f}")
    print(f"  Max drawdown      : ${s.max_drawdown_usd:,.2f} ({s.max_drawdown_pct:.2f}%)")
    print(f"  Final equity      : ${s.final_equity:,.2f}")
    print()
