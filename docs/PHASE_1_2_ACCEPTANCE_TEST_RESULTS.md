# Phase 1 & 2 — Acceptance Test Results

**Date:** 2026-11-10  
**Branch:** main  
**Python:** 3.13.5 | pytest 9.1.1 | MetaTrader5 5.0.6231 | MT5 build 6308

---

## Summary

| Gate | Status |
|------|--------|
| G1 — All unit tests pass (≥ 59) | PASS — 59/59 |
| G2 — M15 comparison ≥ 95% coverage | PASS — 100.0% (166/166) |
| Integration tests (MT5 live) | PASS — 4/4 |
| **Total suite** | **PASS — 63/63** |

---

## Acceptance Fix Tasks Completed

| Task | Description | Result |
|------|-------------|--------|
| 1 | `Candle.__post_init__` raises `ValueError` on naive timestamps | PASS |
| 2 | `aggregate_m5_to_m15` returns `AggregationResult` with incomplete windows | PASS |
| 3 | `validate_candles` warns on naive timestamps; TypeError guarded | PASS |
| 4 | `LiquidityZone` adds `SWEPT` status; sweep_evidence; source_pivots | PASS |
| 5 | Integration test `test_m15_comparison` with dynamic date range | PASS |
| 6 | `CandleCache.load()` validates JSON sidecar (symbol/timeframe/range) | PASS |
| 7 | Timezone test file `test_timezone.py` added | PASS |

---

## Unit Test Results (59/59)

```
tests/unit/test_cache.py::test_save_and_load_matching_metadata         PASSED
tests/unit/test_cache.py::test_load_mismatched_symbol_returns_none     PASSED
tests/unit/test_cache.py::test_load_mismatched_timeframe_returns_none  PASSED
tests/unit/test_cache.py::test_load_uncovered_range_returns_none       PASSED
tests/unit/test_cache.py::test_refresh_causes_next_load_to_return_none PASSED
tests/unit/test_cache.py::test_load_missing_sidecar_returns_none       PASSED
tests/unit/test_cache.py::test_load_no_file_returns_none               PASSED

tests/unit/test_candles.py::test_three_m5_to_one_m15                  PASSED
tests/unit/test_candles.py::test_six_m5_to_two_m15                    PASSED
tests/unit/test_candles.py::test_incomplete_group_reported             PASSED
tests/unit/test_candles.py::test_empty_input                           PASSED
tests/unit/test_candles.py::test_m15_timestamp_is_15min_boundary       PASSED
tests/unit/test_candles.py::test_m15_volume_sum_spread_max             PASSED
tests/unit/test_candles.py::test_window_with_two_m5_reported_as_incomplete PASSED
tests/unit/test_candles.py::test_out_of_order_m5_in_window_raises      PASSED
tests/unit/test_candles.py::test_candles_spanning_boundary_split_correctly PASSED
tests/unit/test_candles.py::test_naive_timestamp_raises                PASSED

tests/unit/test_liquidity_zones.py::test_one_swing_high_creates_one_bsl_zone              PASSED
tests/unit/test_liquidity_zones.py::test_one_swing_low_creates_one_ssl_zone               PASSED
tests/unit/test_liquidity_zones.py::test_two_equal_highs_within_tolerance_merge_into_one_zone PASSED
tests/unit/test_liquidity_zones.py::test_two_swing_highs_far_apart_create_two_zones       PASSED
tests/unit/test_liquidity_zones.py::test_bsl_zone_invalidated_when_candle_closes_above_upper PASSED
tests/unit/test_liquidity_zones.py::test_ssl_zone_invalidated_when_candle_closes_below_lower PASSED
tests/unit/test_liquidity_zones.py::test_get_zones_known_at_no_look_ahead                 PASSED
tests/unit/test_liquidity_zones.py::test_no_zones_on_flat_series                          PASSED
tests/unit/test_liquidity_zones.py::test_bsl_zone_swept_when_wick_above_and_close_inside  PASSED
tests/unit/test_liquidity_zones.py::test_bsl_zone_invalidated_no_sweep_evidence           PASSED
tests/unit/test_liquidity_zones.py::test_ssl_zone_swept_when_wick_below_and_close_inside  PASSED
tests/unit/test_liquidity_zones.py::test_ssl_zone_invalidated_no_sweep_evidence           PASSED
tests/unit/test_liquidity_zones.py::test_zone_stays_active_when_price_does_not_reach_boundary PASSED
tests/unit/test_liquidity_zones.py::test_swept_zone_not_invalidated_by_later_candle       PASSED
tests/unit/test_liquidity_zones.py::test_transitive_cluster_three_pivots_one_zone         PASSED
tests/unit/test_liquidity_zones.py::test_new_pivot_within_zone_expands_not_duplicates     PASSED
tests/unit/test_liquidity_zones.py::test_repeated_identical_pivot_price_one_zone          PASSED
tests/unit/test_liquidity_zones.py::test_zone_has_source_pivots_populated                 PASSED
tests/unit/test_liquidity_zones.py::test_zone_events_have_required_fields                 PASSED
tests/unit/test_liquidity_zones.py::test_invalidation_event_recorded_with_reason          PASSED

tests/unit/test_swings.py::test_swing_high_confirmed_at_index_plus_right   PASSED
tests/unit/test_swings.py::test_swing_low_confirmed_at_index_plus_right    PASSED
tests/unit/test_swings.py::test_pivot_not_returned_when_insufficient_right_bars PASSED
tests/unit/test_swings.py::test_equal_high_on_right_disqualifies_pivot     PASSED
tests/unit/test_swings.py::test_equal_high_on_left_disqualifies_pivot      PASSED
tests/unit/test_swings.py::test_insufficient_candles_returns_empty         PASSED
tests/unit/test_swings.py::test_get_pivots_known_at_filters_correctly      PASSED
tests/unit/test_swings.py::test_no_pivots_on_flat_series                   PASSED

tests/unit/test_timezone.py::test_naive_timestamp_raises_on_candle_creation PASSED
tests/unit/test_timezone.py::test_aware_timestamp_accepted                  PASSED
tests/unit/test_timezone.py::test_epoch_seconds_converts_to_utc             PASSED
tests/unit/test_timezone.py::test_validate_candles_warns_on_naive_timestamp PASSED
tests/unit/test_timezone.py::test_validate_catches_mixed_aware_and_naive    PASSED

tests/unit/test_validation.py::test_valid_series_passes                PASSED
tests/unit/test_validation.py::test_out_of_order_detected              PASSED
tests/unit/test_validation.py::test_duplicate_timestamp_detected       PASSED
tests/unit/test_validation.py::test_ohlc_high_less_than_open           PASSED
tests/unit/test_validation.py::test_ohlc_low_greater_than_close        PASSED
tests/unit/test_validation.py::test_ohlc_high_less_than_low            PASSED
tests/unit/test_validation.py::test_weekday_gap_detected               PASSED
tests/unit/test_validation.py::test_weekend_gap_not_flagged            PASSED
tests/unit/test_validation.py::test_empty_series_no_crash              PASSED

59 passed in 0.42s
```

---

## Integration Test Results (4/4)

MT5 terminal was running and connected to broker. All integration tests executed against live data.

```
tests/integration/test_mt5_data.py::test_mt5_connection                PASSED
tests/integration/test_mt5_data.py::test_fetch_small_range             PASSED
tests/integration/test_mt5_data.py::test_m15_comparison               PASSED
tests/integration/test_mt5_data.py::test_symbol_metadata_no_hardcoding PASSED

4 passed in 1.03s
```

### test_mt5_connection
- MT5 build: 6308  
- connected: True

### test_fetch_small_range
- Fetched: 500 M5 candles  
- Range: 2026-11-06 → 2026-11-10  
- All timestamps UTC-aware: YES  
- All timeframe == "M5": YES

### test_m15_comparison (Gate G2)
- M5 candles fetched: 500  
- Native M15 candles: 166  
- Derived M15 candles: 166  
- Incomplete windows: 1 (current time boundary — expected, last window may be open)
- **Matched: 166 / 166**  
- **Mismatched: 0**  
- **Coverage: 100.0%** ✓ (threshold ≥ 95%)

### test_symbol_metadata_no_hardcoding
- digits: 2 (from MT5, not hardcoded)  
- point: 0.01  
- volume_min: 0.01  
- contract_size: 100.0

---

## Files Changed (Acceptance Fixes)

| File | Change |
|------|--------|
| `src/mt5_liquidity/data/candles.py` | `AggregationResult` dataclass; `aggregate_m5_to_m15` returns it; `Candle.__post_init__` raises on naive ts |
| `src/mt5_liquidity/data/validation.py` | Naive ts warning; TypeError guard on ordering and gap checks |
| `src/mt5_liquidity/data/mt5_adapter.py` | numpy structured array: `dtype.names` check, bracket access, `fromtimestamp(..., tz=utc)` |
| `src/mt5_liquidity/data/cache.py` | JSON sidecar validation on `load()`; `refresh()` method |
| `src/mt5_liquidity/data/history.py` | `compare_m15()` accepts `list[Candle] | AggregationResult` |
| `src/mt5_liquidity/cli.py` | `cmd_fetch()` handles `AggregationResult`; prints incomplete window warnings |
| `src/mt5_liquidity/strategy/models.py` | `SweepEvidence` dataclass; `SWEPT` to `ZoneStatus`; `source_pivots` on `LiquidityZone`; `add_event()` gains `candle_index` + `price` |
| `src/mt5_liquidity/strategy/liquidity_zones.py` | `_UnionFind` for transitive clustering; SWEPT vs INVALIDATED distinction; source_pivots provenance |
| `src/mt5_liquidity/reporting/console.py` | Shows source pivot prices, confirmation timestamps, all event fields, sweep evidence |
| `tests/unit/test_candles.py` | Updated for `AggregationResult`; 4 new tests |
| `tests/unit/test_liquidity_zones.py` | 11 new tests (sweep/invalidate, transitive cluster, no-duplicate, source_pivots, event fields) |
| `tests/unit/test_cache.py` | New file — 7 tests for cache metadata validation |
| `tests/unit/test_timezone.py` | New file — 5 tests for UTC enforcement |
| `tests/integration/test_mt5_data.py` | `_recent_week()` helper; `test_m15_comparison`; `test_symbol_metadata_no_hardcoding` |

---

## Unresolved Issues

None. All 8 acceptance tasks completed. Phase 3 (sweep detection + MSS + retest engine) may proceed.
