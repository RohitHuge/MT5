"""Parquet-based candle cache with JSON metadata sidecars."""
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
        """Return cached candles if the parquet file exists, else None."""
        import pandas as pd

        base = _fname(symbol, timeframe, from_dt, to_dt)
        parquet_path = self._dir / f"{base}.parquet"

        if not parquet_path.exists():
            return None

        df = pd.read_parquet(parquet_path)
        candles: list[Candle] = []
        for _, row in df.iterrows():
            ts = datetime.fromisoformat(str(row["timestamp"]))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
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
        logger.info("Loaded %d candles from cache: %s", len(candles), parquet_path)
        return candles

    def metadata(self, symbol: str, timeframe: str) -> dict | None:
        """Return metadata for the most-recent cached file matching symbol+timeframe."""
        pattern = f"{symbol}_{timeframe}_*.json"
        files = sorted(self._dir.glob(pattern))
        if not files:
            return None
        return json.loads(files[-1].read_text())
