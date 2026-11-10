# Phase 3 Test Results — M15 Sweep + M5 MSS + Retest + Entry Signal

**Date:** 2026-11-10  
**Python:** 3.13.5  
**pytest:** 9.1.1  
**Total tests:** 96 passed, 0 failed  

---

## New files added in Phase 3

| File | Purpose |
|------|---------|
| `src/mt5_liquidity/strategy/sweeps.py` | M15 liquidity sweep detection |
| `src/mt5_liquidity/strategy/market_structure.py` | M5 MSS (Market Structure Shift) detection |
| `src/mt5_liquidity/strategy/retests.py` | Retest + rejection signal detection |
| `src/mt5_liquidity/strategy/setup.py` | Full pipeline + SetupState machine |
| `src/mt5_liquidity/strategy/liquidity_sweep.py` | Top-level strategy entry point |
| `tests/unit/test_sweeps.py` | 7 sweep detection tests |
| `tests/unit/test_market_structure.py` | 5 MSS detection tests |
| `tests/unit/test_retests.py` | 5 retest detection tests |
| `tests/unit/test_setup.py` | 9 full-pipeline state machine tests |

---

## Test results by module

### Integration (4 tests)

| Test | Result |
|------|--------|
| test_mt5_connection | PASS |
| test_fetch_small_range | PASS |
| test_m15_comparison | PASS |
| test_symbol_metadata_no_hardcoding | PASS |

### Unit: test_cache (10 tests)

All 10 PASS — cache save/load, metadata mismatch, range coverage, corruption handling.

### Unit: test_candles (13 tests)

All 13 PASS — M5→M15 aggregation, incomplete windows, boundary splitting, timezone enforcement.

### Unit: test_liquidity_zones (22 tests)

All 22 PASS — zone creation, clustering, invalidation, sweep marking, event logging, union-find transitive grouping.

### Unit: test_swings (8 tests)

All 8 PASS — pivot detection, confirmed_at_index, look-ahead guard, flat series.

### Unit: test_timezone (6 tests) + test_validation (9 tests)

All 15 PASS.

### Unit: test_sweeps (7 tests) — Phase 3

| Test | Result |
|------|--------|
| test_bsl_sweep_detected | PASS |
| test_ssl_sweep_detected | PASS |
| test_false_sweep_wick_no_min_distance | PASS |
| test_breakout_not_a_sweep | PASS |
| test_already_swept_zone_skipped | PASS |
| test_zone_not_yet_created_skipped | PASS |
| test_incomplete_candle_not_used | PASS |

### Unit: test_market_structure (5 tests) — Phase 3

| Test | Result |
|------|--------|
| test_bearish_mss_detected | PASS |
| test_bullish_mss_detected | PASS |
| test_mss_timeout_expires | PASS |
| test_unconfirmed_pivot_not_used | PASS |
| test_mss_records_bars_after_sweep | PASS |

### Unit: test_retests (5 tests) — Phase 3

| Test | Result |
|------|--------|
| test_bearish_retest_detected | PASS |
| test_bullish_retest_detected | PASS |
| test_retest_timeout_expires | PASS |
| test_no_rejection_no_signal | PASS |
| test_entry_price_is_rejection_close | PASS |

### Unit: test_setup (9 tests) — Phase 3

| Test | Result |
|------|--------|
| test_valid_sell_setup_full_pipeline | PASS |
| test_valid_buy_setup_full_pipeline | PASS |
| test_setup_expires_no_mss | PASS |
| test_setup_missed_no_retest | PASS |
| test_one_zone_one_setup | PASS |
| test_no_look_ahead_in_pipeline | PASS |
| test_all_states_recorded_in_events | PASS |
| test_sl_above_sweep_high_for_sell | PASS |
| test_sl_below_sweep_low_for_buy | PASS |

---

## Key implementation notes

### SetupState machine
`SWEEP_CONFIRMED → MSS_CONFIRMED → RETEST_PENDING → ENTRY_READY`  
Terminal states: `EXPIRED` (no MSS within timeout), `MISSED` (MSS found, no retest within timeout).

### No look-ahead bias
- Pivots: `confirmed_at_index` must be **strictly less than** the break candle index.
- Zones: `created_at_index` must be **strictly less than** the sweep candle index.
- Sweeps: processing starts at first M5 bar **after** the M15 sweep candle closes.

### Zone pre-sweep bridge (critical fix)
`build_zones` replays all M15 candles and marks swept zones as `ZoneStatus.SWEPT` with
`SweepEvidence`. `detect_sweeps` now emits `SweepSignal` entries for such pre-swept zones
at the start (from stored evidence), before the real-time replay loop runs. This bridges
Phase 2 zone building with Phase 3 sweep detection without breaking either layer.

### Stop-loss placement
- SELL: `wick_extreme + sl_buffer_points * point` (above sweep high)
- BUY: `wick_extreme - sl_buffer_points * point` (below sweep low)
