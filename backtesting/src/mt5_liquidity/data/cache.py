"""Parquet-based candle cache with JSON metadata sidecars and integrity validation."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .candles import Candle

logger = logging.getLogger(__name__)


def _fname(symbol: str, timeframe: str, from_dt: datetime, to_dt: datetime) -> str:
    return f"{symbol}_{timeframe}_{from_dt:%Y%m%d}_{to_dt:%Y%m%d}"


class CandleCache:
    def __init__(self, cache_dir: str) -> None:
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        candles: list[Candle],
        symbol: str,
        timeframe: str,
        from_dt: datetime,
        to_dt: datetime,
    ) -> Path:
        """Save candles as Parquet with a JSON metadata sidecar."""
        import pandas as pd

        base = _fname(symbol, timeframe, from_dt, to_dt)
        parquet_path = self._dir / f"{base}.parquet"
        meta_path = self._dir / f"{base}.json"

        records = [
            {
                "timestamp": c.timestamp.isoformat(),
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
                "spread": c.spread,
                "timeframe": c.timeframe,
            }
            for c in candles
        ]
        df = pd.DataFrame(records)
        df.to_parquet(parquet_path, index=False)

        meta = {
            "symbol": symbol,
            "timeframe": timeframe,
            "from_dt": from_dt.isoformat(),
            "to_dt": to_dt.isoformat(),
            "candle_count": len(candles),
            "retrieval_ts": datetime.now(timezone.utc).isoformat(),
            "source": "MT5",
        }
        meta_path.write_text(json.dumps(meta, indent=2))
        logger.info("Cached %d candles -> %s", len(candles), parquet_path)
        return parquet_path

    def load(
        self,
        symbol: str,
        timeframe: str,
        from_dt: datetime,
        to_dt: datetime,
    ) -> list[Candle] | None:
        """
        Return cached candles if the Parquet file exists and metadata is valid.

        Validation checks (returns None if any fail):
        1. Parquet file and JSON sidecar both exist.
        2. Cached symbol matches requested symbol.
        3. Cached timeframe matches requested timeframe.
        4. Cached from_dt <= requested from_dt AND cached to_dt >= requested to_dt
           (cached range must fully cover the requested range).
        """
        import pandas as pd

        base = _fname(symbol, timeframe, from_dt, to_dt)
        parquet_path = self._dir / f"{base}.parquet"
        meta_path = self._dir / f"{base}.json"

        if not parquet_path.exists():
            logger.debug("Cache miss (no parquet): %s", parquet_path)
            return None

        # Validate metadata sidecar
        if not meta_path.exists():
            logger.warning("Cache parquet exists but no metadata sidecar: %s", meta_path)
            return None

        try:
            meta = json.loads(meta_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Cannot read cache metadata %s: %s", meta_path, e)
            return None

        if meta.get("symbol") != symbol:
            logger.debug("Cache symbol mismatch: got %s, want %s", meta.get("symbol"), symbol)
            return None

        if meta.get("timeframe") != timeframe:
            logger.debug("Cache timeframe mismatch: got %s, want %s", meta.get("timeframe"), timeframe)
            return None

        try:
            cached_from = datetime.fromisoformat(meta["from_dt"])
            cached_to = datetime.fromisoformat(meta["to_dt"])
        except (KeyError, ValueError) as e:
            logger.warning("Invalid from_dt/to_dt in cache metadata: %s", e)
            return None

        # Ensure timezone-aware for comparison
        if cached_from.tzinfo is None:
            cached_from = cached_from.replace(tzinfo=timezone.utc)
        if cached_to.tzinfo is None:
            cached_to = cached_to.replace(tzinfo=timezone.utc)

        if cached_from > from_dt or cached_to < to_dt:
            logger.debug(
                "Cache range [%s, %s] does not cover requested [%s, %s]",
                cached_from, cached_to, from_dt, to_dt,
            )
            return None

        # Load and validate the Parquet payload. A corrupt or mismatched cache
        # is a cache miss, never a source of silently incorrect historical data.
        try:
            df = pd.read_parquet(parquet_path)
            required = {"timestamp", "open", "high", "low", "close"}
            if not required.issubset(df.columns):
                logger.warning("Cache schema is missing required columns: %s", required - set(df.columns))
                return None

            candles: list[Candle] = []
            for _, row in df.iterrows():
                ts = datetime.fromisoformat(str(row["timestamp"]))
                candles.append(Candle(
                    timestamp=ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 0)),
                    spread=float(row.get("spread", 0)),
                    timeframe=str(row.get("timeframe", timeframe)),
                ))
        except (OSError, ValueError, KeyError, TypeError, ImportError) as exc:
            logger.warning("Invalid or unreadable Parquet cache %s: %s", parquet_path, exc)
            return None

        if len(candles) != meta.get("candle_count"):
            logger.warning(
                "Cache candle count mismatch: metadata=%s parquet=%s",
                meta.get("candle_count"), len(candles),
            )
            return None

        # Verify stored content agrees with the metadata range, then return
        # only bars in the exact requested interval.
        if candles:
            timestamps = [c.timestamp for c in candles]
            if timestamps != sorted(timestamps) or len(set(timestamps)) != len(timestamps):
                logger.warning("Cached candle timestamps are unordered or duplicated: %s", parquet_path)
                return None
            if timestamps[0] < cached_from or timestamps[-1] > cached_to:
                logger.warning("Cached candle timestamps exceed metadata range: %s", parquet_path)
                return None
            if timestamps[0] > cached_from or timestamps[-1] >= cached_to:
                logger.warning("Cached candle timestamps do not cover declared metadata range: %s", parquet_path)
                return None
        elif meta.get("candle_count", 0) != 0:
            logger.warning("Cache declares candles but Parquet payload is empty: %s", parquet_path)
            return None
        requested = [c for c in candles if from_dt <= c.timestamp < to_dt]
        logger.info(
            "Loaded %d/%d candles from cache for requested interval: %s",
            len(requested), len(candles), parquet_path,
        )
        return requested

    def refresh(self, symbol: str, timeframe: str) -> int:
        """
        Delete all Parquet and JSON cache files for the given symbol+timeframe.
        Returns the number of file pairs deleted.
        Forces the next load() call to return None.
        """
        pattern = f"{symbol}_{timeframe}_*.parquet"
        deleted = 0
        for parquet_path in list(self._dir.glob(pattern)):
            meta_path = parquet_path.with_suffix(".json")
            parquet_path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
            deleted += 1
            logger.info("Refreshed (deleted) cache: %s", parquet_path.stem)
        return deleted

    def metadata(self, symbol: str, timeframe: str) -> dict | None:
        """Return metadata for the most-recent cached file matching symbol+timeframe."""
        pattern = f"{symbol}_{timeframe}_*.json"
        files = sorted(self._dir.glob(pattern))
        if not files:
            return None
        return json.loads(files[-1].read_text())
