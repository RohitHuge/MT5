"""Typed analytics data models. Pure data — no I/O, no MT5 imports.

Timezone policy (applies to every model in this package):
- All computation uses UTC-aware timestamps.
- ISO-8601 UTC strings are used in exported files.
- Grouping boundaries (hours, days, ISO weeks, calendar months) are UTC.
- The optional local-hour column is display-only (config.analytics_display_timezone).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class TradeGroupStats:
    """Aggregate performance of a group of closed trades.

    Winners are trades with net_pnl_usd > 0; losers are net_pnl_usd <= 0
    (matching TradeLedger.compute_stats). breakeven is the net == 0 subset
    of losers, reported separately so wins + losses == total always holds.
    """
    trade_count: int = 0
    winners: int = 0
    losers: int = 0
    breakeven: int = 0
    win_rate: float = 0.0            # winners / trade_count, 0.0 when empty
    gross_profit: float = 0.0        # sum of positive gross pnl_usd
    gross_loss: float = 0.0          # sum of non-positive gross pnl_usd (<= 0)
    net_pnl: float = 0.0             # gross_profit + gross_loss - commission
    total_commission: float = 0.0
    avg_net_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0       # gross_profit / |gross_loss|; inf when no losses

    def rounded(self, digits: int = 2) -> "TradeGroupStats":
        return TradeGroupStats(
            trade_count=self.trade_count,
            winners=self.winners,
            losers=self.losers,
            breakeven=self.breakeven,
            win_rate=round(self.win_rate, 4),
            gross_profit=round(self.gross_profit, digits),
            gross_loss=round(self.gross_loss, digits),
            net_pnl=round(self.net_pnl, digits),
            total_commission=round(self.total_commission, digits),
            avg_net_pnl=round(self.avg_net_pnl, digits),
            avg_win=round(self.avg_win, digits),
            avg_loss=round(self.avg_loss, digits),
            profit_factor=self.profit_factor if self.profit_factor == float("inf")
            else round(self.profit_factor, 4),
        )


@dataclass
class DurationBucket:
    """One holding-duration bucket. Bounds are minutes; lower inclusive,
    upper exclusive; the last bucket has upper=None (open-ended, inclusive)."""
    label: str
    min_minutes: float
    max_minutes: float | None
    stats: TradeGroupStats = field(default_factory=TradeGroupStats)


@dataclass
class DurationAnalytics:
    buckets: list[DurationBucket]
    avg_duration_minutes: float | None       # None when no trades
    median_duration_minutes: float | None
    min_duration_minutes: float | None
    max_duration_minutes: float | None
    same_candle_closes: int                  # close_ts on the fill candle (bars_held == 0)
    winners: TradeGroupStats = field(default_factory=TradeGroupStats)
    losers: TradeGroupStats = field(default_factory=TradeGroupStats)
    mae_mfe: str = "unavailable (no intratrade bar-path observations in Phase 2)"


@dataclass
class HourRow:
    hour_utc: int                    # 0-23, canonical
    hour_display: int                # hour in analytics_display_timezone
    display_timezone: str
    stats: TradeGroupStats = field(default_factory=TradeGroupStats)
    # Sum of net P&L of losing trades in this hour (<= 0): how much equity
    # this hour gave back. Documented as "drawdown contribution".
    drawdown_contribution: float = 0.0


@dataclass
class SessionWindow:
    """A DST-aware session window in a regional IANA timezone."""
    name: str
    timezone: str                    # IANA name, e.g. "Europe/London"
    start: str                       # "HH:MM" local time-of-day, inclusive
    end: str                         # "HH:MM" local time-of-day, exclusive

    def describe(self) -> dict:
        return {"name": self.name, "timezone": self.timezone,
                "start": self.start, "end": self.end}


@dataclass
class SessionRow:
    session: str                     # primary-session label (mutually exclusive)
    stats: TradeGroupStats = field(default_factory=TradeGroupStats)


@dataclass
class PeriodRow:
    """One daily/weekly/monthly period.

    Trade attribution: realised cash-flow metrics (trade_count, wins, net_pnl,
    ...) use the trade's close_ts (exit date). Entry-cohort columns
    (entry_trade_count, entry_net_pnl) use fill_ts and are reported separately
    so neither attribution is mixed into the other.
    """
    period_id: str                   # "2026-11-10" | "2026-W46" | "2026-11"
    trade_count: int = 0             # closed in period (exit-attributed)
    winners: int = 0
    losers: int = 0
    breakeven: int = 0
    win_rate: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    net_pnl: float = 0.0
    total_commission: float = 0.0
    avg_net_pnl: float = 0.0
    profit_factor: float = 0.0
    entry_trade_count: int = 0       # filled in period (entry-attributed)
    entry_net_pnl: float = 0.0
    # Mark-to-market equity over the period (from the MTM curve):
    equity_open: float | None = None
    equity_close: float | None = None
    equity_high: float | None = None
    equity_low: float | None = None
    return_pct: float | None = None
    max_drawdown_usd: float | None = None


@dataclass
class DrawdownSummary:
    """Peak/trough/drawdown over a chronological equity series."""
    peak_equity: float | None = None
    peak_ts: datetime | None = None
    trough_equity: float | None = None
    trough_ts: datetime | None = None
    max_drawdown_usd: float = 0.0
    max_drawdown_pct: float = 0.0    # drawdown / peak * 100
    recovery_ts: datetime | None = None     # first time equity returns to peak
    underwater_minutes: float | None = None  # peak_ts -> recovery_ts (or series end)

    def rounded(self, digits: int = 2) -> "DrawdownSummary":
        return DrawdownSummary(
            peak_equity=round(self.peak_equity, digits) if self.peak_equity is not None else None,
            peak_ts=self.peak_ts,
            trough_equity=round(self.trough_equity, digits) if self.trough_equity is not None else None,
            trough_ts=self.trough_ts,
            max_drawdown_usd=round(self.max_drawdown_usd, digits),
            max_drawdown_pct=round(self.max_drawdown_pct, 4),
            recovery_ts=self.recovery_ts,
            underwater_minutes=round(self.underwater_minutes, 1) if self.underwater_minutes is not None else None,
        )


@dataclass
class PeriodStreaks:
    """Profitable/losing streaks at period level (separate from trade-level)."""
    max_profitable_periods: int = 0
    max_losing_periods: int = 0
    profitable_periods: int = 0
    losing_periods: int = 0
    flat_periods: int = 0


@dataclass
class Reconciliation:
    """Accounting integrity checks computed from full-precision values."""
    total_trades: int
    hourly_count_sum: int
    session_count_sum: int
    duration_count_sum: int
    daily_exit_count_sum: int
    daily_entry_count_sum: int
    ledger_net_pnl: float
    daily_net_pnl_sum: float
    weekly_net_pnl_sum: float
    monthly_net_pnl_sum: float
    ledger_gross_profit: float
    hourly_gross_profit_sum: float
    ledger_gross_loss: float
    hourly_gross_loss_sum: float
    final_mtm_equity: float | None
    ledger_final_equity: float
    counts_reconciled: bool
    net_pnl_reconciled: bool
    gross_profit_reconciled: bool
    gross_loss_reconciled: bool
    final_equity_reconciled: bool

    def as_dict(self) -> dict:
        return {
            "total_trades": self.total_trades,
            "hourly_count_sum": self.hourly_count_sum,
            "session_count_sum": self.session_count_sum,
            "duration_count_sum": self.duration_count_sum,
            "daily_exit_count_sum": self.daily_exit_count_sum,
            "daily_entry_count_sum": self.daily_entry_count_sum,
            "ledger_net_pnl": self.ledger_net_pnl,
            "daily_net_pnl_sum": self.daily_net_pnl_sum,
            "weekly_net_pnl_sum": self.weekly_net_pnl_sum,
            "monthly_net_pnl_sum": self.monthly_net_pnl_sum,
            "ledger_gross_profit": self.ledger_gross_profit,
            "hourly_gross_profit_sum": self.hourly_gross_profit_sum,
            "ledger_gross_loss": self.ledger_gross_loss,
            "hourly_gross_loss_sum": self.hourly_gross_loss_sum,
            "final_mtm_equity": self.final_mtm_equity,
            "ledger_final_equity": self.ledger_final_equity,
            "counts_reconciled": self.counts_reconciled,
            "net_pnl_reconciled": self.net_pnl_reconciled,
            "gross_profit_reconciled": self.gross_profit_reconciled,
            "gross_loss_reconciled": self.gross_loss_reconciled,
            "final_equity_reconciled": self.final_equity_reconciled,
        }


@dataclass
class AnalyticsBundle:
    """Complete Phase 2 analytics payload for one backtest run."""
    schema_version: int = 1
    timezone_policy: str = "UTC"
    display_timezone: str = "UTC"
    sessions: list[SessionWindow] = field(default_factory=list)
    attribution_policy: str = ""
    duration: DurationAnalytics | None = None
    hourly: list[HourRow] = field(default_factory=list)
    session_rows: list[SessionRow] = field(default_factory=list)
    daily: list[PeriodRow] = field(default_factory=list)
    weekly: list[PeriodRow] = field(default_factory=list)
    monthly: list[PeriodRow] = field(default_factory=list)
    period_streaks: dict[str, PeriodStreaks] = field(default_factory=dict)
    mtm_drawdown: DrawdownSummary = field(default_factory=DrawdownSummary)
    mtm_points: list[tuple[datetime, float, float, float, int]] = field(default_factory=list)
    # (timestamp, realised_equity, floating_pnl, mtm_equity, open_positions)
    reconciliation: Reconciliation | None = None
    headline: dict = field(default_factory=dict)
