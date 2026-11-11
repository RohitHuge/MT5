"""Persist reproducible backtest reports to a unique local run directory."""
from __future__ import annotations

import csv
import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..backtest.engine import BacktestResult
from ..backtest.ledger import LedgerStats
from ..backtest.engine import SymbolMeta
from ..config import BacktestConfig
from .analytics import (
    ATTRIBUTION_POLICY,
    AnalyticsBundle,
    build_analytics_bundle,
)

logger = logging.getLogger(__name__)

TRADE_FIELDS = [
    "setup_id", "direction", "fill_ts", "close_ts", "fill_price", "close_price",
    "sl_price", "tp_price", "lots", "pnl_usd", "commission_usd", "net_pnl_usd",
    "exit_reason", "fill_candle_index", "close_candle_index", "bars_held",
    "value_per_point_per_lot", "point",
]

ANALYTICS_SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 3
ANALYTICS_FILES = (
    "duration_analysis.csv",
    "hourly_performance.csv",
    "session_performance.csv",
    "daily_performance.csv",
    "weekly_performance.csv",
    "monthly_performance.csv",
    "mark_to_market_equity.csv",
    "analytics_summary.json",
)
RISK_FILES = ("risk_events.csv",)

RISK_EVENT_FIELDS = [
    "timestamp_utc", "event_type", "state", "reason", "realised_equity",
    "floating_pnl", "mtm_equity", "daily_pnl", "threshold_value",
    "observed_value", "peak_equity", "floor_equity", "open_positions",
    "affected_positions", "action",
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


def _pf(pf: float) -> str | float:
    """CSV-safe profit factor: JSON/CSV have no Infinity, use 'inf'."""
    return "inf" if pf == float("inf") else pf


def _pf_safe_dict(payload: dict[str, Any]) -> dict[str, Any]:
    """Replace inf profit factors so JSON stays standards-compliant."""
    if payload.get("profit_factor") == float("inf"):
        payload["profit_factor"] = "inf"
    return payload


def _group_csv_row(stats, extra: dict[str, Any]) -> dict[str, Any]:
    s = stats.rounded()
    row = {
        "trade_count": s.trade_count,
        "winners": s.winners,
        "losers": s.losers,
        "breakeven": s.breakeven,
        "win_rate": s.win_rate,
        "gross_profit": s.gross_profit,
        "gross_loss": s.gross_loss,
        "net_pnl": s.net_pnl,
        "total_commission": s.total_commission,
        "avg_net_pnl": s.avg_net_pnl,
        "avg_win": s.avg_win,
        "avg_loss": s.avg_loss,
        "profit_factor": _pf(s.profit_factor),
    }
    row.update(extra)
    return row


def _write_analytics_dir(run_dir: Path, bundle: AnalyticsBundle) -> None:
    """Write every Phase 2 analytics file under <run_dir>/analytics/."""
    a_dir = run_dir / "analytics"
    a_dir.mkdir(parents=True, exist_ok=False)

    def w_csv(name: str, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
        with (a_dir / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    # Duration buckets
    d = bundle.duration
    w_csv(
        "duration_analysis.csv",
        ["bucket", "min_minutes", "max_minutes", "trade_count", "winners",
         "losers", "breakeven", "win_rate", "gross_profit", "gross_loss",
         "net_pnl", "total_commission", "avg_net_pnl", "avg_win", "avg_loss",
         "profit_factor", "mae_mfe"],
        [
            _group_csv_row(b.stats, {
                "bucket": b.label,
                "min_minutes": b.min_minutes,
                "max_minutes": b.max_minutes if b.max_minutes is not None else "",
                "mae_mfe": d.mae_mfe,
            })
            for b in d.buckets
        ],
    )

    # Hourly (all 24 UTC hours, including zero-trade hours)
    w_csv(
        "hourly_performance.csv",
        ["hour_utc", f"hour_{bundle.display_timezone.lower()}", "trade_count",
         "winners", "losers", "breakeven", "win_rate", "gross_profit",
         "gross_loss", "net_pnl", "total_commission", "avg_net_pnl",
         "avg_win", "avg_loss", "profit_factor", "drawdown_contribution"],
        [
            _group_csv_row(r.stats, {
                "hour_utc": r.hour_utc,
                f"hour_{bundle.display_timezone.lower()}": r.hour_display,
                "drawdown_contribution": round(r.drawdown_contribution, 2),
            })
            for r in bundle.hourly
        ],
    )

    # Sessions (mutually exclusive primary classification)
    w_csv(
        "session_performance.csv",
        ["session", "trade_count", "winners", "losers", "breakeven",
         "win_rate", "gross_profit", "gross_loss", "net_pnl",
         "total_commission", "avg_net_pnl", "avg_win", "avg_loss",
         "profit_factor"],
        [
            _group_csv_row(r.stats, {"session": r.session})
            for r in bundle.session_rows
        ],
    )

    # Daily / weekly / monthly
    period_fields = [
        "period_id", "trade_count", "winners", "losers", "breakeven",
        "win_rate", "gross_profit", "gross_loss", "net_pnl",
        "total_commission", "avg_net_pnl", "profit_factor",
        "entry_trade_count", "entry_net_pnl",
        "equity_open", "equity_close", "equity_high", "equity_low",
        "return_pct", "max_drawdown_usd",
    ]
    for name, rows in (
        ("daily_performance.csv", bundle.daily),
        ("weekly_performance.csv", bundle.weekly),
        ("monthly_performance.csv", bundle.monthly),
    ):
        w_csv(
            name,
            period_fields,
            [{k: ("" if getattr(r, k) is None else getattr(r, k))
              for k in period_fields} for r in rows],
        )

    # Mark-to-market equity (separate file; equity_curve.csv keeps its
    # existing closed-trade meaning).
    with (a_dir / "mark_to_market_equity.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp_utc", "realised_equity", "floating_pnl",
                         "mtm_equity", "open_positions"])
        for ts, realised, floating, mtm, n_open in bundle.mtm_points:
            writer.writerow((ts.isoformat(), realised, floating, mtm, n_open))

    # Analytics summary (schema, policies, headline metrics, reconciliation)
    dd = bundle.mtm_drawdown.rounded()
    summary = {
        "schema_version": ANALYTICS_SCHEMA_VERSION,
        "timezone_policy": bundle.timezone_policy,
        "display_timezone": bundle.display_timezone,
        "attribution": bundle.attribution_policy,
        "sessions": [w.describe() for w in bundle.sessions],
        "duration_summary": {
            "avg_duration_minutes": round(d.avg_duration_minutes, 2) if d.avg_duration_minutes is not None else None,
            "median_duration_minutes": round(d.median_duration_minutes, 2) if d.median_duration_minutes is not None else None,
            "min_duration_minutes": round(d.min_duration_minutes, 2) if d.min_duration_minutes is not None else None,
            "max_duration_minutes": round(d.max_duration_minutes, 2) if d.max_duration_minutes is not None else None,
            "same_candle_closes": d.same_candle_closes,
            "winners": _pf_safe_dict(asdict(d.winners.rounded())),
            "losers": _pf_safe_dict(asdict(d.losers.rounded())),
            "mae_mfe": d.mae_mfe,
        },
        "mark_to_market_drawdown": {
            "peak_equity": dd.peak_equity,
            "peak_ts": dd.peak_ts,
            "trough_equity": dd.trough_equity,
            "trough_ts": dd.trough_ts,
            "max_drawdown_usd": dd.max_drawdown_usd,
            "max_drawdown_pct": dd.max_drawdown_pct,
            "recovery_ts": dd.recovery_ts,
            "underwater_minutes": dd.underwater_minutes,
            "note": "End-of-candle marks only; intrabar drawdown not observable from M5 OHLC.",
        },
        "period_streaks": {
            key: {
                "profitable_periods": s.profitable_periods,
                "losing_periods": s.losing_periods,
                "flat_periods": s.flat_periods,
                "max_profitable_periods": s.max_profitable_periods,
                "max_losing_periods": s.max_losing_periods,
            }
            for key, s in bundle.period_streaks.items()
        },
        "headline": bundle.headline,
        "reconciliation": bundle.reconciliation.as_dict() if bundle.reconciliation else None,
    }
    _write_json(a_dir / "analytics_summary.json", summary)


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
    analytics_bundle: AnalyticsBundle | None = None,
) -> Path:
    """Write one immutable report bundle and return its directory.

    The timestamp is UTC. A UUID suffix prevents collisions when runs start in
    the same second. Report files are local outputs and are not tracked by Git.

    schema_version 2 adds the analytics/ subdirectory (duration, hourly,
    session, daily/weekly/monthly performance, mark-to-market equity, and an
    analytics summary). Existing schema-version-1 files keep their names and
    meaning; equity_curve.csv remains the closed-trade (realised) equity.
    """
    started_at = run_timestamp or datetime.now(timezone.utc)
    if started_at.tzinfo is None:
        raise ValueError("run_timestamp must be timezone-aware")
    started_at = started_at.astimezone(timezone.utc)
    run_id = f"{started_at:%Y-%m-%d_%H-%M-%S}_{uuid4().hex[:8]}"
    run_dir = Path(reports_dir) / symbol / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    bundle = analytics_bundle or build_analytics_bundle(result, config)
    _write_analytics_dir(run_dir, bundle)

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
        "risk": result.risk_summary,
    }
    # JSON has no standard Infinity value; store a stable string for zero-loss runs.
    if summary["statistics"]["profit_factor"] == float("inf"):
        summary["statistics"]["profit_factor"] = "inf"
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

    # Phase 3: structured risk-event log (one row per protection event).
    with (run_dir / "risk_events.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RISK_EVENT_FIELDS)
        writer.writeheader()
        for event in result.risk_events:
            writer.writerow({
                "timestamp_utc": event.timestamp.isoformat(),
                "event_type": event.event_type,
                "state": event.state,
                "reason": event.reason,
                "realised_equity": event.realised_equity,
                "floating_pnl": event.floating_pnl,
                "mtm_equity": event.mtm_equity,
                "daily_pnl": event.daily_pnl if event.daily_pnl is not None else "",
                "threshold_value": event.threshold_value if event.threshold_value is not None else "",
                "observed_value": event.observed_value if event.observed_value is not None else "",
                "peak_equity": event.peak_equity if event.peak_equity is not None else "",
                "floor_equity": event.floor_equity if event.floor_equity is not None else "",
                "open_positions": event.open_positions,
                "affected_positions": ";".join(event.affected_positions),
                "action": event.action,
            })

    config_payload = asdict(config)
    # Never persist MT5 secrets, even if a caller has populated them in config.
    config_payload.pop("mt5_password", None)
    config_payload.pop("mt5_login", None)
    metadata = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_id": run_id,
        "generated_at_utc": started_at,
        "symbol": symbol,
        "date_range": {"from": from_date, "to": to_date},
        "config": config_payload,
        "symbol_metadata": asdict(symbol_meta),
        "analytics": {
            "schema_version": ANALYTICS_SCHEMA_VERSION,
            "timezone_policy": bundle.timezone_policy,
            "display_timezone": bundle.display_timezone,
            "attribution": ATTRIBUTION_POLICY,
            "sessions": [w.describe() for w in bundle.sessions],
        },
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
        "risk": {
            "enabled": config.account_risk.enabled,
            "open_position_action": config.account_risk.open_position_action,
            "daily_reset_timezone": config.account_risk.daily_reset_timezone,
            "note": "Account-level protection settings; thresholds are "
                    "experimental inputs, not recommended parameters.",
        },
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

    expected_files = (
        "summary.json", "trades.csv", "equity_curve.csv", "metadata.json",
        "execution.log", "risk_events.csv",
    )
    missing = [name for name in expected_files if not (run_dir / name).is_file()]
    missing += [
        f"analytics/{name}" for name in ANALYTICS_FILES
        if not (run_dir / "analytics" / name).is_file()
    ]
    if missing:
        raise OSError(f"Backtest report is incomplete; missing files: {', '.join(missing)}")
    logger.info("Backtest report saved to %s", run_dir)
    return run_dir
