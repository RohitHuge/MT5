"""Persist reproducible backtest reports to a unique local run directory."""
from __future__ import annotations

import csv
import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..backtest.engine import BacktestResult
from ..backtest.ledger import LedgerStats
from ..backtest.engine import SymbolMeta
from ..config import BacktestConfig

logger = logging.getLogger(__name__)

TRADE_FIELDS = [
    "setup_id", "direction", "fill_ts", "close_ts", "fill_price", "close_price",
    "sl_price", "tp_price", "lots", "pnl_usd", "commission_usd", "net_pnl_usd",
    "exit_reason", "fill_candle_index", "close_candle_index", "bars_held",
    "value_per_point_per_lot", "point",
]


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "value"):
        return value.value
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=_json_default, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def export_backtest_report(
    result: BacktestResult,
    symbol: str,
    from_date: str,
    to_date: str,
    config: BacktestConfig,
    symbol_meta: SymbolMeta,
    incomplete_m15_windows: list[dict[str, Any]] | None = None,
    reports_dir: str | Path = "reports",
    run_timestamp: datetime | None = None,
) -> Path:
    """Write one immutable report bundle and return its directory.

    The timestamp is UTC. A UUID suffix prevents collisions when runs start in
    the same second. Report files are local outputs and are not tracked by Git.
    """
    started_at = run_timestamp or datetime.now(timezone.utc)
    if started_at.tzinfo is None:
        raise ValueError("run_timestamp must be timezone-aware")
    started_at = started_at.astimezone(timezone.utc)
    run_id = f"{started_at:%Y-%m-%d_%H-%M-%S}_{uuid4().hex[:8]}"
    run_dir = Path(reports_dir) / symbol / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    stats: LedgerStats = result.stats
    summary = {
        "symbol": symbol,
        "from_date": from_date,
        "to_date": to_date,
        "run_id": run_id,
        "generated_at_utc": started_at,
        "setups": {
            "total": result.setups_total,
            "entry_ready": result.setups_entry_ready,
            "skipped_max_positions": result.setups_skipped_max_pos,
            "skipped_daily_loss": result.setups_skipped_daily_loss,
            "skipped_sizing": result.setups_skipped_sizing,
            "skipped_no_next_bar": result.setups_skipped_no_next_bar,
        },
        "trades_opened": result.trades_opened,
        "force_closed": result.force_closed,
        "statistics": asdict(stats),
    }
    _write_json(run_dir / "summary.json", summary)

    with (run_dir / "trades.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRADE_FIELDS)
        writer.writeheader()
        for trade in result.closed_positions:
            row = asdict(trade)
            row["net_pnl_usd"] = trade.net_pnl_usd
            writer.writerow({key: row.get(key) for key in TRADE_FIELDS})

    with (run_dir / "equity_curve.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp_utc", "equity"])
        writer.writerows((ts.isoformat(), equity) for ts, equity in result.equity_curve)

    config_payload = asdict(config)
    # Never persist MT5 secrets, even if a caller has populated them in config.
    config_payload.pop("mt5_password", None)
    config_payload.pop("mt5_login", None)
    metadata = {
        "schema_version": 1,
        "run_id": run_id,
        "generated_at_utc": started_at,
        "symbol": symbol,
        "date_range": {"from": from_date, "to": to_date},
        "config": config_payload,
        "symbol_metadata": asdict(symbol_meta),
        "data_quality": {
            "incomplete_m15_windows": [
                {
                    "window_start": item.get("window_start"),
                    "expected": item.get("expected"),
                    "got": item.get("got"),
                }
                for item in (incomplete_m15_windows or [])
            ]
        },
        "cost_assumptions": {
            "spread_points": config.spread_points,
            "slippage_points": config.slippage_points,
            "commission_usd_per_lot_per_side": config.commission_usd,
            "swap": "excluded",
            "note": stats.cost_note,
        },
        "position_sizing_rejections": [
            asdict(item) for item in result.rejected_sizings
        ],
    }
    _write_json(run_dir / "metadata.json", metadata)

    # Keep a concise per-run log independent of the caller's terminal output.
    log_lines = [
        f"Backtest report created: {started_at.isoformat()}",
        f"Symbol: {symbol}",
        f"Period: {from_date} -> {to_date}",
        f"Trades opened: {result.trades_opened}",
        f"Trades closed: {stats.total_trades}",
        f"Net P&L: {stats.net_pnl:.2f}",
        f"Final equity: {stats.final_equity:.2f}",
        f"Warnings: {len(incomplete_m15_windows or [])} incomplete M15 windows",
    ]
    (run_dir / "execution.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    expected_files = ("summary.json", "trades.csv", "equity_curve.csv", "metadata.json", "execution.log")
    missing = [name for name in expected_files if not (run_dir / name).is_file()]
    if missing:
        raise OSError(f"Backtest report is incomplete; missing files: {', '.join(missing)}")
    logger.info("Backtest report saved to %s", run_dir)
    return run_dir
