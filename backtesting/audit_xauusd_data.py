#!/usr/bin/env python3
"""Read-only XAUUSD M5/M15 data-quality audit using the logged-in MT5 terminal."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import MetaTrader5 as mt5
except ImportError:
    print(
        "MetaTrader5 is missing. Install with: "
        "E:\\Python\\python.exe -m pip install MetaTrader5",
        file=sys.stderr,
    )
    raise SystemExit(2)

UTC = timezone.utc
FRAMES = {"M5": (mt5.TIMEFRAME_M5, 300), "M15": (mt5.TIMEFRAME_M15, 900)}
COLUMNS = [
    "time_epoch", "time_utc", "open", "high", "low", "close",
    "tick_volume", "spread_points", "real_volume",
]


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="XAUUSD", help="Exact broker symbol, e.g. XAUUSD.a")
    parser.add_argument("--from-date", help="Inclusive UTC start date (YYYY-MM-DD)")
    parser.add_argument("--to-date", help="Inclusive UTC end date (YYYY-MM-DD)")
    parser.add_argument("--output-dir", default="audit_reports")
    return parser.parse_args()


def date_range(args):
    today = datetime.now(UTC).date()
    try:
        first = datetime.strptime(args.from_date, "%Y-%m-%d").date() if args.from_date else today - timedelta(days=89)
        last = datetime.strptime(args.to_date, "%Y-%m-%d").date() if args.to_date else today
    except ValueError as exc:
        raise SystemExit(f"Invalid date. Use YYYY-MM-DD. Details: {exc}")
    if last < first:
        raise SystemExit("--to-date must be on or after --from-date")
    start = datetime(first.year, first.month, first.day, tzinfo=UTC)
    end_exclusive = datetime(last.year, last.month, last.day, tzinfo=UTC) + timedelta(days=1)
    return start, end_exclusive


def convert_rates(rates):
    result = []
    for item in rates:
        stamp = int(item["time"])
        result.append({
            "time_epoch": stamp,
            "time_utc": datetime.fromtimestamp(stamp, UTC).isoformat(),
            "open": float(item["open"]),
            "high": float(item["high"]),
            "low": float(item["low"]),
            "close": float(item["close"]),
            "tick_volume": int(item["tick_volume"]),
            "spread_points": int(item["spread"]),
            "real_volume": int(item["real_volume"]),
        })
    return result


def save_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    pos = (len(values) - 1) * fraction
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return values[lo]
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def audit_timeframe(rows, label, seconds):
    stamps = [r["time_epoch"] for r in rows]
    counts = Counter(stamps)
    duplicates = [
        {"time_utc": datetime.fromtimestamp(t, UTC).isoformat(), "count": n}
        for t, n in counts.items() if n > 1
    ]
    order_errors = [
        {
            "previous_utc": datetime.fromtimestamp(a, UTC).isoformat(),
            "current_utc": datetime.fromtimestamp(b, UTC).isoformat(),
        }
        for a, b in zip(stamps, stamps[1:]) if b <= a
    ]

    bad_ohlc, bad_prices, gaps = [], [], []
    spreads = []
    for row in rows:
        o, h, l, c = (row[k] for k in ("open", "high", "low", "close"))
        if not all(math.isfinite(v) for v in (o, h, l, c)) or h < max(o, l, c) or l > min(o, h, c):
            bad_ohlc.append(row["time_utc"])
        if min(o, h, l, c) <= 0:
            bad_prices.append(row["time_utc"])
        spreads.append(row["spread_points"])

    for a, b in zip(stamps, stamps[1:]):
        if b - a > seconds:
            prev = datetime.fromtimestamp(a, UTC)
            nxt = datetime.fromtimestamp(b, UTC)
            weekend = prev.weekday() >= 4 and nxt.weekday() <= 0
            gaps.append({
                "previous_utc": prev.isoformat(),
                "next_utc": nxt.isoformat(),
                "gap_minutes": round((b - a) / 60, 2),
                "missing_bar_estimate": max(0, (b - a) // seconds - 1),
                "classification": "likely_weekend_or_market_closure" if weekend else "review_against_broker_session_hours",
            })

    median = statistics.median(spreads) if spreads else None
    p95 = percentile(spreads, 0.95)
    spread_threshold = max(median * 3, p95) if median is not None and median > 0 else None
    outliers = [
        {"time_utc": r["time_utc"], "spread_points": r["spread_points"]}
        for r in rows if spread_threshold is not None and r["spread_points"] > spread_threshold
    ]

    return {
        "timeframe": label,
        "candle_count": len(rows),
        "first_candle_utc": rows[0]["time_utc"] if rows else None,
        "last_candle_utc": rows[-1]["time_utc"] if rows else None,
        "duplicate_timestamp_count": len(duplicates),
        "duplicate_samples": duplicates[:30],
        "out_of_order_count": len(order_errors),
        "out_of_order_samples": order_errors[:30],
        "invalid_ohlc_count": len(bad_ohlc),
        "invalid_ohlc_samples": bad_ohlc[:30],
        "non_positive_price_count": len(bad_prices),
        "non_positive_price_samples": bad_prices[:30],
        "zero_tick_volume_count": sum(r["tick_volume"] == 0 for r in rows),
        "gap_count": len(gaps),
        "gap_samples": gaps[:100],
        "spread_points": {
            "minimum": min(spreads) if spreads else None,
            "median": median,
            "p95": p95,
            "maximum": max(spreads) if spreads else None,
            "zero_spread_count": sum(v == 0 for v in spreads),
            "outlier_count": len(outliers),
            "outlier_samples": outliers[:30],
        },
    }


def compare_timeframes(m5, m15, tolerance):
    native = {r["time_epoch"]: r for r in m15}
    buckets = {}
    for row in m5:
        start = (row["time_epoch"] // 900) * 900
        buckets.setdefault(start, []).append(row)

    incomplete, missing_native, ohlc_mismatch, volume_mismatch = [], [], [], []
    compared = 0
    for start, group in sorted(buckets.items()):
        group.sort(key=lambda r: r["time_epoch"])
        expected = [start, start + 300, start + 600]
        if [r["time_epoch"] for r in group] != expected:
            incomplete.append(datetime.fromtimestamp(start, UTC).isoformat())
            continue
        m15_bar = native.get(start)
        if m15_bar is None:
            missing_native.append(datetime.fromtimestamp(start, UTC).isoformat())
            continue
        compared += 1
        aggregate = {
            "open": group[0]["open"],
            "high": max(r["high"] for r in group),
            "low": min(r["low"] for r in group),
            "close": group[-1]["close"],
        }
        diffs = {k: aggregate[k] - m15_bar[k] for k in aggregate}
        if any(abs(v) > tolerance for v in diffs.values()):
            ohlc_mismatch.append({
                "time_utc": m15_bar["time_utc"],
                "m5_aggregate": aggregate,
                "native_m15": {k: m15_bar[k] for k in aggregate},
                "differences": diffs,
            })
        m5_volume = sum(r["tick_volume"] for r in group)
        if m5_volume != m15_bar["tick_volume"]:
            volume_mismatch.append({
                "time_utc": m15_bar["time_utc"],
                "m5_tick_volume_sum": m5_volume,
                "native_m15_tick_volume": m15_bar["tick_volume"],
            })

    return {
        "complete_m5_groups_compared": compared,
        "incomplete_m5_groups_count": len(incomplete),
        "incomplete_m5_group_samples": incomplete[:30],
        "complete_groups_without_native_m15_count": len(missing_native),
        "complete_groups_without_native_m15_samples": missing_native[:30],
        "ohlc_mismatch_count": len(ohlc_mismatch),
        "ohlc_mismatch_samples": ohlc_mismatch[:30],
        "tick_volume_mismatch_count": len(volume_mismatch),
        "tick_volume_mismatch_samples": volume_mismatch[:30],
        "price_tolerance": tolerance,
        "note": "M5 OHLC aggregation is compared with native M15 OHLC. Tick-volume differences are reported separately because broker aggregation conventions may differ.",
    }


def main():
    args = arguments()
    start, end_exclusive = date_range(args)
    out = Path(args.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    if not mt5.initialize():
        print(f"MT5 initialize failed: {mt5.last_error()}", file=sys.stderr)
        return 2

    try:
        account = mt5.account_info()
        if account is None:
            print("No logged-in MT5 account detected. Open MT5 and log in first.", file=sys.stderr)
            return 2

        info = mt5.symbol_info(args.symbol)
        if info is None:
            matches = [s.name for s in (mt5.symbols_get() or []) if "XAU" in s.name.upper() or "GOLD" in s.name.upper()]
            print(f"Symbol {args.symbol!r} not found. Gold-like symbols: {matches[:30]}", file=sys.stderr)
            return 2
        if not mt5.symbol_select(args.symbol, True):
            print(f"Could not select {args.symbol}: {mt5.last_error()}", file=sys.stderr)
            return 2

        info = mt5.symbol_info(args.symbol)
        tolerance = max(float(info.point or 0), float(info.trade_tick_size or 0), 1e-8)
        datasets = {}
        # MT5 Python uses UTC timestamps. copy_rates_range has an inclusive end.
        end_inclusive = end_exclusive - timedelta(seconds=1)

        for label, (timeframe, seconds) in FRAMES.items():
            rates = mt5.copy_rates_range(args.symbol, timeframe, start, end_inclusive)
            if rates is None:
                print(f"Failed to fetch {label}: {mt5.last_error()}", file=sys.stderr)
                return 2
            rows = convert_rates(rates)
            datasets[label] = rows
            save_csv(out / f"{args.symbol}_{label}.csv", rows)

        m5_report = audit_timeframe(datasets["M5"], "M5", 300)
        m15_report = audit_timeframe(datasets["M15"], "M15", 900)
        comparison = compare_timeframes(datasets["M5"], datasets["M15"], tolerance)

        critical, warnings = [], []
        for report in (m5_report, m15_report):
            tf = report["timeframe"]
            if not report["candle_count"]:
                critical.append(f"{tf}: no candles downloaded")
            for key, message in [
                ("duplicate_timestamp_count", "duplicate timestamps"),
                ("out_of_order_count", "out-of-order timestamps"),
                ("invalid_ohlc_count", "invalid OHLC candles"),
                ("non_positive_price_count", "non-positive prices"),
            ]:
                if report[key]:
                    critical.append(f"{tf}: {report[key]} {message}")
            if report["gap_count"]:
                warnings.append(f"{tf}: {report['gap_count']} time gaps need session-hours review")
            if report["spread_points"]["outlier_count"]:
                warnings.append(f"{tf}: {report['spread_points']['outlier_count']} unusual spread observations")
        if comparison["ohlc_mismatch_count"]:
            critical.append(f"M5/M15 OHLC mismatches: {comparison['ohlc_mismatch_count']}")
        if comparison["incomplete_m5_groups_count"]:
            warnings.append(f"Incomplete M5-to-M15 groups: {comparison['incomplete_m5_groups_count']}")
        if comparison["complete_groups_without_native_m15_count"]:
            warnings.append("Some complete M5 groups have no matching native M15 candle")
        if comparison["tick_volume_mismatch_count"]:
            warnings.append(f"M5/M15 tick-volume differences: {comparison['tick_volume_mismatch_count']}")

        verdict = "REVIEW REQUIRED" if critical else ("PASS WITH WARNINGS" if warnings else "NO ISSUES DETECTED IN CHECKS")
        report = {
            "audit_created_utc": datetime.now(UTC).isoformat(),
            "symbol": args.symbol,
            "broker_server": getattr(account, "server", None),
            "date_range_utc": {"start_inclusive": start.isoformat(), "end_inclusive": end_inclusive.isoformat()},
            "timezone_note": "MT5 epoch timestamps are converted to UTC. The broker's chart display timezone is not inferred.",
            "symbol_metadata": {
                "name": info.name, "description": info.description, "digits": info.digits,
                "point": info.point, "trade_tick_size": info.trade_tick_size,
                "trade_contract_size": info.trade_contract_size,
                "currency_base": info.currency_base, "currency_profit": info.currency_profit,
                "currency_margin": info.currency_margin,
            },
            "m5": m5_report,
            "m15": m15_report,
            "m5_m15_comparison": comparison,
            "verdict": verdict,
            "critical_findings": critical,
            "warnings": warnings,
            "interpretation": [
                "A time gap is not automatically a missing candle; check broker trading sessions and daily breaks.",
                "Spread outliers are review candidates, not automatically corrupt data.",
                "Tick-volume sums may differ from native M15 due to broker aggregation conventions.",
                "This audit evaluates data integrity, not strategy profitability.",
            ],
        }

        json_path = out / f"{args.symbol}_audit_report.json"
        json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        text_path = out / f"{args.symbol}_audit_report.txt"
        lines = [
            f"{args.symbol} HISTORICAL DATA QUALITY AUDIT",
            "=" * 48,
            f"Verdict: {verdict}",
            f"Broker server: {report['broker_server']}",
            f"UTC range: {start.isoformat()} to {end_inclusive.isoformat()}",
            f"Symbol digits / point / tick size: {info.digits} / {info.point} / {info.trade_tick_size}",
            "",
            f"M5 candles: {m5_report['candle_count']:,}",
            f"M15 candles: {m15_report['candle_count']:,}",
            "",
        ]
        for item in (m5_report, m15_report):
            sp = item["spread_points"]
            lines += [
                f"{item['timeframe']} CHECKS",
                f"Duplicates: {item['duplicate_timestamp_count']}",
                f"Out of order: {item['out_of_order_count']}",
                f"Invalid OHLC: {item['invalid_ohlc_count']}",
                f"Non-positive prices: {item['non_positive_price_count']}",
                f"Time gaps: {item['gap_count']}",
                f"Spread points min/median/p95/max: {sp['minimum']} / {sp['median']} / {sp['p95']} / {sp['maximum']}",
                f"Spread outliers: {sp['outlier_count']}",
                "",
            ]
        lines += [
            "M5 -> M15 CONSISTENCY",
            f"Complete groups compared: {comparison['complete_m5_groups_compared']}",
            f"Incomplete groups: {comparison['incomplete_m5_groups_count']}",
            f"Missing native M15 bars: {comparison['complete_groups_without_native_m15_count']}",
            f"OHLC mismatches: {comparison['ohlc_mismatch_count']}",
            f"Tick-volume mismatches: {comparison['tick_volume_mismatch_count']}",
            "",
            "CRITICAL FINDINGS:",
            *([f"- {x}" for x in critical] or ["- None detected"]),
            "",
            "WARNINGS:",
            *([f"- {x}" for x in warnings] or ["- None detected"]),
            "",
            "NOTE: Gaps require broker-session review. The report does not establish strategy profitability.",
            "Files: M5 CSV, M15 CSV, JSON report, TXT report.",
        ]
        text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        print(f"Connected MT5 server: {report['broker_server']}")
        print(f"Symbol: {info.name} | digits={info.digits} | point={info.point} | tick_size={info.trade_tick_size}")
        print(f"M5 candles: {m5_report['candle_count']:,} | M15 candles: {m15_report['candle_count']:,}")
        print(f"AUDIT VERDICT: {verdict}")
        print(f"Text report: {text_path}")
        print(f"JSON report: {json_path}")
        print(f"CSV files: {out / (args.symbol + '_M5.csv')} and {out / (args.symbol + '_M15.csv')}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
