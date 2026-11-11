"""Central configuration for the backtester. Credentials loaded from .env only."""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv


@dataclass
class AccountRiskConfig:
    """Account-level protection settings (Phase 3). Disabled by default.

    All thresholds are optional; a rule is active only when at least one of
    its forms is set. When both a USD and a percentage form are set for the
    same rule, whichever threshold is crossed first triggers (the tighter
    one). Example values are experimental settings, not recommended or
    proven-profitable parameters.
    """
    enabled: bool = False
    # Daily profit protection (locks in the day's result; resets next day).
    daily_target_usd: float | None = None
    daily_target_pct: float | None = None        # % of the daily baseline equity
    # Daily loss protection (realised + floating P&L; resets next day).
    # None falls back to the legacy BacktestConfig.max_daily_loss_pct so the
    # pre-existing guard semantics carry over unchanged.
    daily_loss_usd: float | None = None
    daily_loss_pct: float | None = None
    daily_reset_timezone: str = "UTC"            # IANA zone for the daily boundary
    # Account-wide equity trailing protection from the high-water mark
    # (never resets automatically).
    equity_trail_giveback_usd: float | None = None
    equity_trail_giveback_pct: float | None = None   # % of peak equity
    # Account-wide maximum drawdown from the high-water mark (never resets
    # automatically; requires an explicit manual reset).
    max_drawdown_usd: float | None = None
    max_drawdown_pct: float | None = None
    # Consecutive losing trades (net P&L < 0) allowed before new entries are
    # locked until the next daily reset; a breakeven trade breaks the streak.
    max_consecutive_losses: int | None = None
    # Pre-trade ceiling on a single trade's monetary risk (either form; OR).
    max_risk_per_trade_usd: float | None = None
    max_risk_per_trade_pct: float | None = None  # % of current MTM equity
    # What happens to open positions when any protection triggers.
    open_position_action: str = "STOP_NEW_ENTRIES"   # or "CLOSE_ALL"


def validate_account_risk_config(cfg: AccountRiskConfig) -> str | None:
    """Return an error string for invalid account-risk configuration, else None."""
    if cfg.open_position_action not in ("STOP_NEW_ENTRIES", "CLOSE_ALL"):
        return (f"open_position_action must be 'STOP_NEW_ENTRIES' or 'CLOSE_ALL', "
                f"got {cfg.open_position_action!r}")
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(cfg.daily_reset_timezone)
    except Exception as exc:
        return f"Unknown daily_reset_timezone {cfg.daily_reset_timezone!r}: {exc}"

    numeric_rules = {
        "daily_target_usd": cfg.daily_target_usd,
        "daily_target_pct": cfg.daily_target_pct,
        "daily_loss_usd": cfg.daily_loss_usd,
        "daily_loss_pct": cfg.daily_loss_pct,
        "equity_trail_giveback_usd": cfg.equity_trail_giveback_usd,
        "equity_trail_giveback_pct": cfg.equity_trail_giveback_pct,
        "max_drawdown_usd": cfg.max_drawdown_usd,
        "max_drawdown_pct": cfg.max_drawdown_pct,
        "max_risk_per_trade_usd": cfg.max_risk_per_trade_usd,
        "max_risk_per_trade_pct": cfg.max_risk_per_trade_pct,
    }
    for name, value in numeric_rules.items():
        if value is not None and value <= 0:
            return f"{name} must be positive, got {value}"
    for name in ("daily_target_pct", "daily_loss_pct", "equity_trail_giveback_pct",
                 "max_drawdown_pct", "max_risk_per_trade_pct"):
        value = getattr(cfg, name)
        if value is not None and value > 100:
            return f"{name} must be <= 100 percent, got {value}"
    if cfg.max_consecutive_losses is not None and cfg.max_consecutive_losses < 1:
        return f"max_consecutive_losses must be >= 1, got {cfg.max_consecutive_losses}"
    if cfg.max_risk_per_trade_usd is None and cfg.max_risk_per_trade_pct is None:
        pass  # per-trade ceiling simply inactive
    return None


@dataclass
class BacktestConfig:
    mt5_path: str = r"C:\Program Files\MetaTrader 5\terminal64.exe"
    mt5_login: int = 0
    mt5_password: str = ""
    mt5_server: str = ""
    symbol: str = "XAUUSD"
    cache_dir: str = "data/processed"
    raw_dir: str = "data/raw"
    log_dir: str = "logs"
    pivot_left: int = 2
    pivot_right: int = 2
    zone_tolerance_points: int = 10
    sweep_min_points: int = 5
    mss_timeout_bars: int = 20
    retest_timeout_bars: int = 10
    retest_band_points: int = 5
    sl_buffer_points: int = 5
    risk_pct: float = 0.5
    rr_target: float = 2.0
    max_positions: int = 1
    spread_points: int = 21
    commission_usd: float = 0.0  # per-lot, per-side; round trip = 2 * lots * rate
    slippage_points: int = 3    # adverse entry slippage
    exit_slippage_points: int = 0  # adverse exit slippage; zero preserves legacy assumption
    max_daily_loss_pct: float = 3.0
    initial_equity: float = 10_000.0
    min_stop_distance_points: float = 5.0
    max_lots_per_trade: float = 1.0
    analytics_display_timezone: str = "UTC"
    duration_bucket_minutes: tuple[int, ...] | None = None
    session_overrides: dict[str, dict[str, str]] | None = None
    # Phase 3: account-level risk manager (disabled by default; when disabled
    # the engine keeps its legacy inline daily-loss guard).
    account_risk: AccountRiskConfig = field(default_factory=AccountRiskConfig)


def load_config(env_path: str | None = None) -> BacktestConfig:
    if env_path:
        load_dotenv(env_path)
    else:
        load_dotenv()
    return BacktestConfig(
        mt5_path=os.getenv("MT5_PATH", r"C:\Program Files\MetaTrader 5\terminal64.exe"),
        mt5_login=int(os.getenv("MT5_LOGIN", "0") or "0"),
        mt5_password=os.getenv("MT5_PASSWORD", ""),
        mt5_server=os.getenv("MT5_SERVER", ""),
    )
