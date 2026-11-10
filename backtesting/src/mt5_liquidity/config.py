"""Central configuration for the backtester. Credentials loaded from .env only."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class BacktestConfig:
    # MT5 connection
    mt5_path: str = r"C:\Program Files\MetaTrader 5\terminal64.exe"
    mt5_login: int = 0
    mt5_password: str = ""
    mt5_server: str = ""

    # Symbol
    symbol: str = "XAUUSD"

    # Data
    cache_dir: str = "data/processed"
    raw_dir: str = "data/raw"
    log_dir: str = "logs"

    # Strategy — Phase 2
    pivot_left: int = 2
    pivot_right: int = 2
    zone_tolerance_points: int = 10

    # Phase 3
    sweep_min_points: int = 5
    mss_timeout_bars: int = 20
    retest_timeout_bars: int = 10
    retest_band_points: int = 5   # points either side of structure level for retest band
    sl_buffer_points: int = 5     # buffer above/below sweep extreme for stop-loss

    # Backtest execution
    risk_pct: float = 0.5
    rr_target: float = 2.0
    max_positions: int = 1
    spread_points: int = 21
    commission_usd: float = 0.0
    slippage_points: int = 3
    max_daily_loss_pct: float = 3.0
    initial_equity: float = 10_000.0


def load_config(env_path: str | None = None) -> BacktestConfig:
    """Load config with MT5 credentials from .env. All other fields use safe defaults."""
    if env_path:
        load_dotenv(env_path)
    else:
        # Walk up to find .env
        load_dotenv()

    cfg = BacktestConfig(
        mt5_path=os.getenv("MT5_PATH", r"C:\Program Files\MetaTrader 5\terminal64.exe"),
        mt5_login=int(os.getenv("MT5_LOGIN", "0") or "0"),
        mt5_password=os.getenv("MT5_PASSWORD", ""),
        mt5_server=os.getenv("MT5_SERVER", ""),
    )
    return cfg
