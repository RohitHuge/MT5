"""Terminal output helpers. Only this layer uses print()."""
from __future__ import annotations

from ..data.candles import Candle
from ..data.validation import ValidationResult
from ..strategy.models import LiquidityZone, ZoneStatus


def print_header(title: str) -> None:
    width = 60
    print("=" * width)
    print(f"  {title}")
    print("=" * width)


def print_terminal_info(info: dict) -> None:
    print_header("MT5 TERMINAL INFO")
    for k, v in info.items():
        print(f"  {k:<20}: {v}")
    print()


def print_symbol_info(info: dict) -> None:
    print_header(f"SYMBOL: {info.get('name', '?')}")
    for k, v in info.items():
        print(f"  {k:<20}: {v}")
    print()


def print_data_summary(
    candles_m5: list[Candle],
    validation: ValidationResult,
    m15_comparison: dict,
) -> None:
    print_header("DATA SUMMARY")
    print(f"  M5 candles          : {validation.candle_count}")
    print(f"  Validation passed   : {validation.passed}")
    print(f"  Duplicate timestamps: {len(validation.duplicate_timestamps)}")
    print(f"  OHLC violations     : {len(validation.ohlc_violations)}")
    print(f"  Gaps detected       : {len(validation.gaps)}")
    print(f"  Out of order        : {len(validation.out_of_order)}")

    if validation.warnings:
        print("\n  Warnings:")
        for w in validation.warnings[:10]:
            print(f"    - {w}")

    if m15_comparison:
        print(f"\n  M15 comparison:")
        print(f"    Matched     : {m15_comparison.get('matched', '?')}")
        print(f"    Mismatched  : {len(m15_comparison.get('mismatched', []))}")
        print(f"    Coverage    : {m15_comparison.get('coverage_pct', '?')}%")
    print()


def print_zones_report(zones: list[LiquidityZone], candles: list[Candle]) -> None:
    print_header("LIQUIDITY ZONES")
    active = [z for z in zones if z.status == ZoneStatus.ACTIVE]
    invalidated = [z for z in zones if z.status == ZoneStatus.INVALIDATED]
    print(f"  Total zones     : {len(zones)}")
    print(f"  Active          : {len(active)}")
    print(f"  Invalidated     : {len(invalidated)}")
    print()

    for z in zones:
        status_str = z.status.value
        print(
            f"  [{status_str:<12}] {z.zone_type.value}  "
            f"upper={z.upper:.2f}  lower={z.lower:.2f}  "
            f"created_idx={z.created_at_index}  "
            f"ts={z.created_at.strftime('%Y-%m-%d %H:%M')}"
        )
        # Provenance: source pivots
        for p in z.source_pivots:
            print(
                f"    pivot idx={p.index}  price={p.price}  "
                f"confirmed_at={p.confirmed_at.strftime('%Y-%m-%d %H:%M')}"
            )
        # Status-transition events
        for ev in z.events:
            print(
                f"    [{ev['type']:<12}] idx={ev.get('candle_index', '?')}  "
                f"price={ev.get('price', 0):.4f}  {ev.get('detail', '')}"
            )
        # Sweep evidence
        if z.sweep_evidence is not None:
            se = z.sweep_evidence
            print(
                f"    [SWEEP EVIDENCE] wick={se.wick_extreme}  "
                f"close={se.close_price}  idx={se.candle_index}"
            )
        print()
    print()
