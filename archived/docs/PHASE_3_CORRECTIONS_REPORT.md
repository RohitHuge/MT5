# Phase 3 Corrections Report — F1–F4

**Date:** 2026-11-10  
**Branch:** main  
**Baseline:** 96 tests passing → **105 tests passing** after corrections

---

## Summary

Four bugs were identified and corrected in the Phase 3 strategy engine. All fixes are surgical and
preserve the existing Phase 3 API surface; no Phase 4 work was started.

---

## F1 — Pre-swept zone bridge bypassed `min_sweep_points` check (Critical)

**File:** `src/mt5_liquidity/strategy/sweeps.py`

**Problem:**  
`detect_sweeps()` has a bridge that emits `SweepSignal` objects for zones that were already marked
`SWEPT` by `build_zones()`. This bridge emitted a signal without validating that the wick distance
exceeded `min_sweep_points * point`. A zone swept by a barely-qualifying wick in `build_zones`
(which applies no distance minimum) could produce a signal that the live path would have rejected.

**Fix:**  
Added an explicit distance check to the pre-swept bridge, mirroring the live-path rule exactly:
- BSL: `wick_extreme - zone.upper >= min_dist`
- SSL: `zone.lower - wick_extreme >= min_dist`

Zones that fail the check are skipped with a `DEBUG` log line.

**New tests (8):**

| Test | Covers |
|---|---|
| `test_pre_swept_bsl_distance_sufficient_emits_signal` | BSL bridge emits when distance ≥ min |
| `test_pre_swept_bsl_distance_insufficient_no_signal` | BSL bridge rejects when distance < min |
| `test_pre_swept_ssl_distance_sufficient_emits_signal` | SSL bridge emits when distance ≥ min |
| `test_pre_swept_ssl_distance_insufficient_no_signal` | SSL bridge rejects when distance < min |
| `test_pre_swept_bsl_five_ticks_above_upper_emits` | 6-tick wick clears 5-tick threshold |
| `test_pre_swept_bsl_one_tick_below_minimum_no_signal` | 4-tick wick rejected by 5-tick threshold |
| `test_pre_swept_zone_without_evidence_skipped` | No `sweep_evidence` → bridge skips safely |
| `test_pre_swept_signals_sorted_chronologically_with_live` | Mixed bridge+live signals sorted |

---

## F2 — `entry_price` conflated signal reference price with execution fill (Critical)

**Files:** `src/mt5_liquidity/strategy/retests.py`, `src/mt5_liquidity/strategy/setup.py`

**Problem:**  
`RetestSignal.entry_price` and `Setup.entry_price` stored `rejection_candle.close` and labelled it
the fill price. This is semantically incorrect: the rejection candle's close is the **signal price**
(the price at which the signal fired); the actual fill price is determined in Phase 4 when an order
is submitted. Using the same field name for both makes backtester P&L calculations incorrect.

**Fix:**  
Renamed `entry_price` → `signal_price` in:
- `RetestSignal` dataclass field (with updated docstring: `# Phase 4 will determine actual fill`)
- Both BSL and SSL constructor sites in `detect_retest()`
- `Setup` dataclass field
- `run_pipeline()` assignment and transition detail string

**Updated tests:**

| Test file | Lines updated |
|---|---|
| `test_retests.py` | `result.entry_price` → `result.signal_price` (3 assertions) |
| `test_retests.py` | Renamed `test_entry_price_is_rejection_close` → `test_signal_price_equals_rejection_close` |
| `test_retests.py` | Added `assert not hasattr(result, "entry_price")` guard |
| `test_setup.py` | `s.entry_price` → `s.signal_price` (2 assertions) |

---

## F3 — `_find_m5_index_after_m15_close` used M15 open time instead of close time (Critical)

**File:** `src/mt5_liquidity/strategy/setup.py`

**Problem:**  
The function that finds the first M5 bar eligible for MSS detection compared
`c.timestamp > m15_sweep_ts` (the M15 candle's **open** time). MT5 timestamps are open times, so
an M15 candle opening at 11:15 closes at 11:30. M5 candles at 11:20 and 11:25 overlap the
still-open M15 sweep candle and must not be included — including them is a form of look-ahead bias,
because the M15 bar is not yet complete at those times.

**Fix:**  
Changed comparison to `c.timestamp >= m15_sweep_ts + timedelta(minutes=15)`:
- Added `timedelta` to the `datetime` import
- Updated docstring to explain the M15 close-time rule

**Fixture impact (`test_setup.py`):**  
M15 idx 13 opens at 11:15 (= M5 idx 39), closes at 11:30 (= M5 idx 42).  
Fixtures were updated: MSS candle moved from M5 idx 40 → 42; retest from idx 42 → 44.  
Assertion values (close prices) are unchanged.

**New tests (2, in `test_setup.py`):**

| Test | Covers |
|---|---|
| `test_m5_inside_m15_window_excluded_from_mss` | M5 at 11:20 and 11:25 ignored; setup EXPIRED |
| `test_m5_at_m15_close_time_included_in_mss` | M5 at exact M15 close time (11:30) eligible |

---

## F4 — Zone objects mutated by `run_pipeline()` making second call non-deterministic (High)

**File:** `src/mt5_liquidity/strategy/setup.py`

**Problem:**  
`detect_sweeps()` mutates the `zones` list it receives by updating `zone.status` and
`zone.sweep_evidence`. Calling `run_pipeline()` twice with the same `zones` object produced
inconsistent behaviour because the second call saw zones with state from the first call.

**Fix:**  
Added `zones = copy.deepcopy(zones)` at the start of `run_pipeline()` before `detect_sweeps()` is
called. The caller's zone objects are now never mutated.

**New tests (3, in `test_setup.py`):**

| Test | Covers |
|---|---|
| `test_run_pipeline_idempotent_same_setups` | Two calls with same zones return same setups |
| `test_run_pipeline_does_not_mutate_caller_zones` | Zone status and event count unchanged after call |
| `test_run_pipeline_does_not_mutate_active_zones` | ACTIVE zones remain ACTIVE after call |

---

## Test Results

```
============================= test session starts =============================
platform win32 — Python 3.13.5, pytest-9.1.1

collected 105 items

tests/unit/test_cache.py                 10 passed
tests/unit/test_candles.py              12 passed
tests/unit/test_liquidity_zones.py      21 passed
tests/unit/test_market_structure.py      5 passed
tests/unit/test_retests.py               5 passed
tests/unit/test_setup.py               14 passed
tests/unit/test_sweeps.py              15 passed
tests/unit/test_swings.py               8 passed
tests/unit/test_timezone.py             6 passed
tests/unit/test_validation.py           9 passed

105 passed in 1.36s
```

**Baseline (before corrections):** 96 tests  
**After corrections:** 105 tests (+9 new tests net; 1 test renamed)

---

## Acceptance Decision

**PASS** — All four critical/high issues are resolved with matching tests. No regressions.  
Phase 4 may proceed.
