"""MT5 connection and data retrieval adapter."""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from ..config import BacktestConfig

logger = logging.getLogger(__name__)

CHUNK_SIZE = 50_000


class MT5Adapter:
    def __init__(self, config: BacktestConfig) -> None:
        self._config = config
        self._mt5 = None

    def _get_mt5(self):
        if self._mt5 is None:
            import MetaTrader5 as mt5
            self._mt5 = mt5
        return self._mt5

    def connect(self) -> bool:
        mt5 = self._get_mt5()
        cfg = self._config
        ok = mt5.initialize(
            path=cfg.mt5_path,
            login=cfg.mt5_login if cfg.mt5_login else None,
            password=cfg.mt5_password if cfg.mt5_password else None,
            server=cfg.mt5_server if cfg.mt5_server else None,
        )
        if not ok:
            logger.error("MT5 initialize failed: %s", mt5.last_error())
            return False

        info = mt5.terminal_info()
        account = mt5.account_info()
        logger.info(
            "MT5 connected | build=%s | broker=%s | server=%s | account=%s",
            info.build if info else "?",
            account.company if account else "?",
            account.server if account else "?",
            account.login if account else "?",
        )
        return True

    def disconnect(self) -> None:
        mt5 = self._get_mt5()
        mt5.shutdown()
        logger.info("MT5 disconnected.")

    def search_symbols(self, pattern: str = "") -> list[dict]:
        mt5 = self._get_mt5()
        symbols = mt5.symbols_get(pattern) if pattern else mt5.symbols_get()
        if symbols is None:
            return []
        return [
            {
                "name": s.name,
                "description": s.description,
                "digits": s.digits,
                "point": s.point,
            }
            for s in symbols
        ]

    def get_symbol_info(self, symbol: str) -> dict:
        mt5 = self._get_mt5()
        info = mt5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"Symbol not found: {symbol} | {mt5.last_error()}")
        return {
            "name": info.name,
            "digits": info.digits,
            "point": info.point,
            "tick_size": info.trade_tick_size,
            "tick_value": info.trade_tick_value,
            "contract_size": info.trade_contract_size,
            "volume_min": info.volume_min,
            "volume_max": info.volume_max,
            "volume_step": info.volume_step,
            "spread": info.spread,
            "trade_mode": info.trade_mode,
            "stops_level": info.trade_stops_level,
        }

    def get_rates(
        self,
        symbol: str,
        timeframe_const,
        from_dt: datetime,
        to_dt: datetime,
    ) -> list[dict]:
        mt5 = self._get_mt5()
        all_rates: list[dict] = []
        chunk_start = from_dt

        while chunk_start < to_dt:
            rates = mt5.copy_rates_range(symbol, timeframe_const, chunk_start, to_dt)
            if rates is None or len(rates) == 0:
                logger.warning(
                    "No rates returned for %s from %s to %s | error=%s",
                    symbol, chunk_start, to_dt, mt5.last_error(),
                )
                break

            # MT5 returns a numpy structured array; access fields by name, not .get()
            field_names = rates.dtype.names if hasattr(rates, "dtype") else []
            has_tick_volume = "tick_volume" in field_names
            has_spread = "spread" in field_names

            for r in rates:
                # fromtimestamp with tz=UTC converts POSIX epoch correctly
                # regardless of broker local timezone.
                ts_utc = datetime.fromtimestamp(int(r["time"]), tz=timezone.utc)
                all_rates.append({
                    "timestamp": ts_utc,
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                    "volume": float(r["tick_volume"]) if has_tick_volume else 0.0,
                    "spread": float(r["spread"]) if has_spread else 0.0,
                })

            last_ts = datetime.fromtimestamp(int(rates[-1]["time"]), tz=timezone.utc)
            if len(rates) < CHUNK_SIZE or last_ts >= to_dt:
                break
            chunk_start = last_ts + timedelta(seconds=1)

        # Deduplicate by timestamp
        seen: set[datetime] = set()
        unique: list[dict] = []
        for r in all_rates:
            if r["timestamp"] not in seen:
                seen.add(r["timestamp"])
                unique.append(r)

        logger.info(
            "Fetched %d candles for %s [%s -> %s]",
            len(unique), symbol,
            unique[0]["timestamp"] if unique else "?",
            unique[-1]["timestamp"] if unique else "?",
        )
        return unique

    def get_terminal_info(self) -> dict:
        mt5 = self._get_mt5()
        info = mt5.terminal_info()
        account = mt5.account_info()
        if info is None:
            return {}
        return {
            "build": info.build,
            "path": info.path,
            "connected": info.connected,
            "broker": account.company if account else "unknown",
            "server": account.server if account else "unknown",
        }
