"""Central configuration for the backtester. Credentials loaded from .env only."""
from __future__ import annotations
import os
from dataclasses import dataclass
from dotenv import load_dotenv


@dataclass
class BacktestConfig:
    mt5_path: str = r"C:\\Program Files\\MetaTrader 5\\terminal64.exe"
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


def load_config(env_path: str | None = None) -> BacktestConfig:
    if env_path:
        load_dotenv(env_path)
    else:
        load_dotenv()
    return BacktestConfig(
        mt5_path=os.getenv("MT5_PATH", r"C:\\Program Files\\MetaTrader 5\\terminal64.exe"),
        mt5_login=int(os.getenv("MT5_LOGIN", "0") or "0"),
        mt5_password=os.getenv("MT5_PASSWORD", ""),
        mt5_server=os.getenv("MT5_SERVER", ""),
    )
