"""CLI entry point — four subcommands: inspect, fetch, zones, backtest."""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

from .config import load_config
from .data.candles import aggregate_m5_to_m15
from .data.cache import CandleCache
from .data.history import fetch_m5, fetch_m15_native, compare_m15
from .data.mt5_adapter import MT5Adapter
from .data.validation import validate_candles
from .reporting.console import (
    print_data_summary,
    print_header,
    print_symbol_info,
    print_terminal_info,
    print_zones_report,
)
from .reporting.metrics import print_backtest_report
from .strategy.liquidity_zones import build_zones
from .strategy.swings import detect_pivots

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def _parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def cmd_inspect(args: argparse.Namespace) -> int:
    cfg = load_config()
    adapter = MT5Adapter(cfg)
    if not adapter.connect():
        print("ERROR: Could not connect to MT5.", file=sys.stderr)
        return 1
    try:
        print_terminal_info(adapter.get_terminal_info())
        print_symbol_info(adapter.get_symbol_info(args.symbol))
    finally:
        adapter.disconnect()
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    cfg = load_config()
    from_dt = _parse_date(args.from_date)
    to_dt = _parse_date(args.to_date)
    cache = CandleCache(cfg.cache_dir)

    adapter = MT5Adapter(cfg)
    if not adapter.connect():
        print("ERROR: Could not connect to MT5.", file=sys.stderr)
        return 1

    try:
        # M5
        m5 = cache.load(args.symbol, "M5", from_dt, to_dt)
        if m5 is None:
            print(f"Fetching M5 data for {args.symbol} [{from_dt.date()} -> {to_dt.date()}]...")
            m5 = fetch_m5(adapter, args.symbol, from_dt, to_dt)
            cache.save(m5, args.symbol, "M5", from_dt, to_dt)
        else:
            print(f"Loaded {len(m5)} M5 candles from cache.")

        val_m5 = validate_candles(m5, "M5")

        # Derived M15
        agg_result = aggregate_m5_to_m15(m5)
        m15_derived = agg_result.candles
        if agg_result.incomplete_windows:
            print(f"  Warning: {len(agg_result.incomplete_windows)} incomplete M15 window(s) detected.")
            for w in agg_result.incomplete_windows[:5]:
                print(f"    {w['window_start'].isoformat()} — expected 3, got {w['got']}")
        val_m15 = validate_candles(m15_derived, "M15")

        # Native M15 comparison
        m15_native = fetch_m15_native(adapter, args.symbol, from_dt, to_dt)
        comparison = compare_m15(m15_derived, m15_native)  # accepts list[Candle]

        print_data_summary(m5, val_m5, comparison)

        if not val_m5.passed:
            print("ERROR: M5 data quality check failed.", file=sys.stderr)
            return 1
    finally:
        adapter.disconnect()
    return 0


def cmd_zones(args: argparse.Namespace) -> int:
    cfg = load_config()
    cfg.pivot_left = args.pivot_left
    cfg.pivot_right = args.pivot_right
    from_dt = _parse_date(args.from_date)
    to_dt = _parse_date(args.to_date)
    cache = CandleCache(cfg.cache_dir)

    # Try cache first; fall back to live fetch
    m5 = cache.load(args.symbol, "M5", from_dt, to_dt)
    if m5 is None:
        adapter = MT5Adapter(cfg)
        if not adapter.connect():
            print("ERROR: Could not connect to MT5 and no cache found.", file=sys.stderr)
            return 1
        try:
            m5 = fetch_m5(adapter, args.symbol, from_dt, to_dt)
            cache.save(m5, args.symbol, "M5", from_dt, to_dt)
        finally:
            adapter.disconnect()

    sym_info: dict = {}
    try:
        adapter2 = MT5Adapter(cfg)
        if adapter2.connect():
            sym_info = adapter2.get_symbol_info(args.symbol)
            adapter2.disconnect()
    except Exception:
        pass

    point = sym_info.get("point")
    if point is None or point <= 0:
        print(
            f"ERROR: Could not retrieve valid point metadata for {args.symbol}; "
            "refusing to build zones with a guessed point value.",
            file=sys.stderr,
        )
        return 1
    pivots = detect_pivots(m5, cfg.pivot_left, cfg.pivot_right)
    zones = build_zones(m5, pivots, cfg.zone_tolerance_points, point)
    print_zones_report(zones, m5)
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    cfg = load_config()
    cfg.pivot_left    = args.pivot_left
    cfg.pivot_right   = args.pivot_right
    cfg.risk_pct      = args.risk_pct
    cfg.rr_target     = args.rr_target
    cfg.max_positions = args.max_positions
    cfg.max_daily_loss_pct = args.max_daily_loss_pct
    cfg.initial_equity     = args.equity
    cfg.trading_day_timezone = args.trading_day_timezone
    cfg.commission_usd = args.commission_per_lot_side

    from_dt = _parse_date(args.from_date)
    to_dt   = _parse_date(args.to_date)
    cache   = CandleCache(cfg.cache_dir)

    # Load or fetch M5
    m5 = cache.load(args.symbol, "M5", from_dt, to_dt)
    if m5 is None:
        adapter = MT5Adapter(cfg)
        if not adapter.connect():
            print("ERROR: Could not connect to MT5 and no cache found.", file=sys.stderr)
            return 1
        try:
            m5 = fetch_m5(adapter, args.symbol, from_dt, to_dt)
            cache.save(m5, args.symbol, "M5", from_dt, to_dt)
        finally:
            adapter.disconnect()

    # Derive M15
    agg = aggregate_m5_to_m15(m5)
    m15 = agg.candles
    if agg.incomplete_windows:
        print(f"  Warning: {len(agg.incomplete_windows)} incomplete M15 window(s) dropped from backtest.")

    # Symbol metadata: accept a complete manual set or retrieve all values from MT5.
    from .backtest.engine import SymbolMeta, run_backtest
    manual_values = [args.point, args.tick_value, args.tick_size, args.volume_min, args.volume_max, args.volume_step]
    supplied = [value is not None for value in manual_values]
    if any(supplied) and not all(supplied):
        print("ERROR: Supply all six metadata flags together, or none to retrieve from MT5.", file=sys.stderr)
        return 1
    sym_meta: SymbolMeta | None = None
    if all(supplied):
        sym_meta = SymbolMeta(
            point=args.point, tick_value=args.tick_value, tick_size=args.tick_size,
            volume_min=args.volume_min, volume_max=args.volume_max, volume_step=args.volume_step,
        )
    else:
        adapter3 = MT5Adapter(cfg)
        if adapter3.connect():
            try:
                si = adapter3.get_symbol_info(args.symbol)
                sym_meta = SymbolMeta(
                    point=si["point"],
                    tick_value=si["trade_tick_value"],
                    tick_size=si["trade_tick_size"],
                    volume_min=si["volume_min"],
                    volume_max=si["volume_max"],
                    volume_step=si["volume_step"],
                )
            except (KeyError, TypeError, ValueError) as exc:
                print(f"ERROR: Invalid MT5 metadata response: {exc}", file=sys.stderr)
            finally:
                adapter3.disconnect()
        else:
            print("WARNING: Could not connect to MT5 for symbol metadata.", file=sys.stderr)

    if sym_meta is None:
        print(
            f"ERROR: Could not retrieve symbol metadata for {args.symbol}. "
            "Start the MT5 terminal and ensure the symbol is available, "
            "or supply metadata via --point / --tick-value etc. (see --help).",
            file=sys.stderr,
        )
        return 1

    from .backtest.engine import validate_symbol_meta
    meta_error = validate_symbol_meta(sym_meta, args.symbol)
    if meta_error:
        print(f"ERROR: Invalid symbol metadata — {meta_error}", file=sys.stderr)
        return 1

    result = run_backtest(m15_candles=m15, m5_candles=m5, config=cfg, symbol_meta=sym_meta)
    print_backtest_report(result, args.symbol, args.from_date, args.to_date)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="mt5_liquidity", description="XAUUSD Liquidity Backtester")
    sub = parser.add_subparsers(dest="command", required=True)

    # inspect
    p_inspect = sub.add_parser("inspect", help="Check MT5 connection and symbol metadata")
    p_inspect.add_argument("--symbol", default="XAUUSD")

    # fetch
    p_fetch = sub.add_parser("fetch", help="Fetch and validate historical M5 data")
    p_fetch.add_argument("--symbol", default="XAUUSD")
    p_fetch.add_argument("--from", dest="from_date", required=True)
    p_fetch.add_argument("--to", dest="to_date", required=True)

    # zones
    p_zones = sub.add_parser("zones", help="Detect and report liquidity zones")
    p_zones.add_argument("--symbol", default="XAUUSD")
    p_zones.add_argument("--from", dest="from_date", required=True)
    p_zones.add_argument("--to", dest="to_date", required=True)
    p_zones.add_argument("--pivot-left", type=int, default=2)
    p_zones.add_argument("--pivot-right", type=int, default=2)

    # backtest
    p_bt = sub.add_parser("backtest", help="Run full liquidity-sweep backtest")
    p_bt.add_argument("--symbol", default="XAUUSD")
    p_bt.add_argument("--from", dest="from_date", required=True)
    p_bt.add_argument("--to", dest="to_date", required=True)
    p_bt.add_argument("--pivot-left", type=int, default=2)
    p_bt.add_argument("--pivot-right", type=int, default=2)
    p_bt.add_argument("--risk-pct", type=float, default=0.5)
    p_bt.add_argument("--rr-target", type=float, default=2.0)
    p_bt.add_argument("--max-positions", type=int, default=1)
    p_bt.add_argument("--max-daily-loss-pct", type=float, default=3.0)
    p_bt.add_argument("--equity", type=float, default=10_000.0)
    p_bt.add_argument("--commission-per-lot-side", type=float, default=0.0)
    p_bt.add_argument("--trading-day-timezone", default="UTC", help="IANA timezone for daily loss reset")
    p_bt.add_argument("--point", type=float)
    p_bt.add_argument("--tick-value", type=float)
    p_bt.add_argument("--tick-size", type=float)
    p_bt.add_argument("--volume-min", type=float)
    p_bt.add_argument("--volume-max", type=float)
    p_bt.add_argument("--volume-step", type=float)

    args = parser.parse_args()
    dispatch = {
        "inspect":  cmd_inspect,
        "fetch":    cmd_fetch,
        "zones":    cmd_zones,
        "backtest": cmd_backtest,
    }
    sys.exit(dispatch[args.command](args))


if __name__ == "__main__":
    main()
