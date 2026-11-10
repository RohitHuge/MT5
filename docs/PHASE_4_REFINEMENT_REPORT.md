# Phase 4 Refinement — Implementation Report

**Date:** 2026-11-10  
**Environment:** Windows 11, Python 3.13.5, pytest 9.1.1  
**Package:** `mt5-liquidity` v0.1.0  
**Base commit:** `01ed8db` (Phase 4 initial)

---

## Summary

Six critical correctness issues were identified and fixed in the Phase 4 backtest engine. The engine now produces accurate monetary risk calculations, correct bid/ask pricing for SELL exits, proper commission accounting, entry-candle SL/TP evaluation, unrealised-aware daily loss protection, and strict metadata validation.

| Fix | Category | Severity | Status |
|---|---|---|---|
| F1 — Position sizing uses actual fill price | Risk | High | Fixed |
| F2 — Entry-candle SL/TP evaluation | Execution | High | Fixed |
| F3 — SELL exit on ask side | P&L accuracy | High | Fixed |
| F4 — Commission deducted in P&L | Accounting | Medium | Fixed |
| F5 — Daily loss guard includes floating P&L | Risk | Medium | Fixed |
| F6 — Metadata validation; no hardcoded fallback | Safety | High | Fixed |

**Test count:** 142 (pre-refinement) → **197** (post-refinement)  
**Regressions:** 0

---

## Fix Details

### F1 — Position Sizing Uses Actual Fill Price

**Problem:** `engine.py` called `calculate_position_size(signal_price=setup.signal_price, ...)`. When the fill candle opened at a gap from the signal price, the actual SL distance from the fill differed, so the monetary risk did not match the configured `risk_pct`.

**Fix:** `execution.py` now exports `compute_fill_price()`. The engine calls it *before* sizing to obtain the actual fill price, then passes that to `calculate_position_size`. Sizing is based on `|fill_price - sl_price|`, not `|signal_price - sl_price|`.

**Files changed:** `backtest/execution.py`, `backtest/engine.py`

**Regression tests added (4):**
- `test_sizing_uses_fill_price_not_signal_price` — gap-down BUY shrinks SL distance, more lots
- `test_gap_down_sell_sizing_uses_fill_price` — gap-up SELL shrinks SL distance, more lots
- `test_min_volume_rejection_when_sl_too_wide` — gap away from SL widens distance below volume_min
- `test_compute_fill_price_*` — unit tests for the helper function

---

### F2 — Entry-Candle SL/TP Evaluation

**Problem:** A new position was appended to `open_positions` after the existing-position loop completed. The fill candle was therefore never evaluated for SL/TP, so a position could open and immediately be stopped out on the same candle without the engine recording it.

**Fix:** `OpenPosition.check_entry_candle(candle)` added. After opening a position, the engine calls this method on the fill candle. If SL or TP is hit on the fill candle, the position is closed immediately with `close_candle_index == fill_candle_index`. The gap check is omitted in `check_entry_candle` because the position was just opened at the candle's open price.

**Files changed:** `backtest/positions.py`, `backtest/engine.py`

**Regression tests added (7):**
- BUY SL hit on entry candle
- BUY TP hit on entry candle
- BUY both hit on entry candle (SL wins)
- BUY neither hit on entry candle
- SELL SL hit on entry candle
- SELL TP hit on entry candle
- SELL neither hit on entry candle
- Engine integration: BUY SL on fill candle; SELL TP on fill candle

---

### F3 — Correct SELL Exit Calculations for Spread

**Problem:** Candle OHLC data is bid-based. BUY positions correctly paid spread at entry (ask = bid + spread). SELL positions entered at bid but exited using raw bid prices, missing the ask-side spread cost at exit.

**Fix:** `OpenPosition.check_candle()` now applies bid/ask asymmetry:

| Direction | Entry | SL/TP trigger | Exit price | Gap close |
|---|---|---|---|---|
| BUY | ask = bid_open + (spread+slip)×pt | bid_low ≤ sl, bid_high ≥ tp | sl or tp (bid) | bid_open |
| SELL | bid = bid_open − slip×pt | (bid_high+spread) ≥ sl, (bid_low+spread) ≤ tp | sl or tp (ask) | bid_open+spread |

Spread is therefore paid exactly once per round trip: at BUY entry, at SELL exit.

**Files changed:** `backtest/positions.py`

**Regression tests added (5):**
- SELL SL requires ask-side price to reach sl level
- SELL SL triggered correctly when ask = sl
- SELL TP trigger delayed by spread (key semantic test)
- SELL gap close includes spread
- BUY exits unaffected by `spread_points` setting

---

### F4 — Commission in Trade P&L

**Problem:** `BacktestConfig.commission_usd` existed but was not deducted from P&L or equity.

**Convention:** `commission_usd` is a **per-lot per-side** charge. Round-trip commission = `2 × commission_usd × lots`. Commission is deducted at trade close time.

**Fix:**
- `ClosedPosition.commission_usd: float = 0.0` field added.
- `ClosedPosition.net_pnl_usd` property returns `pnl_usd − commission_usd`.
- Engine computes round-trip commission at close: `2 * config.commission_usd * lots`.
- `equity += closed.net_pnl_usd` (not gross).
- `LedgerStats.total_commission` added.
- `net_pnl` in stats is after commission.
- Winners/losers classified by `net_pnl_usd` (commission can flip a gross win to a net loss).
- Swap remains excluded; `cost_note = "swap excluded (Phase 4)"`.

**Files changed:** `backtest/positions.py`, `backtest/ledger.py`, `backtest/engine.py`, `reporting/metrics.py`

**Regression tests added (10):**
- Zero commission preserves existing behaviour
- Commission deducted from net P&L
- Commission on losing trade increases loss
- Commission can flip winner to loser classification
- Commission accumulates across multiple trades (different lot sizes)
- Round-trip commission in engine integration test

---

### F5 — Daily Loss Protection Considers Floating Losses

**Problem:** The daily loss guard compared `equity` (realised only) against the daily baseline. A large open floating loss was invisible to the guard until the position closed.

**Fix:** `OpenPosition.unrealized_pnl(candle)` method added. The engine computes:

```
current_equity_with_floating = realised_equity + Σ unrealised_pnl(open_positions, current_candle)
daily_loss_pct = (day_baseline − current_equity_with_floating) / day_baseline × 100
```

If `daily_loss_pct ≥ max_daily_loss_pct`, new entries are blocked. Existing open positions are **not** force-closed (documented behaviour: block entries only).

**Unrealised mark price:**
- BUY: `candle.close` (bid)
- SELL: `candle.close + spread_points × point` (ask)

**Files changed:** `backtest/positions.py`, `backtest/engine.py`

**Regression tests added (3):**
- Realised loss blocks new entries (pre-existing test, now also covers floating)
- Floating loss alone blocks new entries
- When floating loss disappears (recovery), new entries are permitted again (via existing test structure)

---

### F6 — Remove Unsafe Hardcoded Symbol Metadata Fallback

**Problem:** `cmd_backtest` in `cli.py` fell back to hardcoded XAUUSD metadata when the MT5 connection failed. This would silently produce misleading results for any other instrument.

**Fix:**
- `validate_symbol_meta(meta, symbol)` function added to `engine.py`.
- Validates: `point > 0`, `tick_size > 0`, `tick_value > 0`, `volume_min > 0`, `volume_step > 0`, `volume_max > 0`, `volume_min ≤ volume_max`, `volume_step ≤ volume_max`.
- `run_backtest()` calls `validate_symbol_meta()` before any processing; raises `ValueError` if invalid.
- `cli.py` no longer provides the hardcoded fallback; prints a clear error and exits with code 1.

**Files changed:** `backtest/engine.py`, `cli.py`

**Regression tests added (13):** in `tests/unit/test_symbol_meta.py`:
- Valid XAUUSD metadata accepted
- Zero/negative `point` rejected
- Zero `tick_size`, `tick_value`, `volume_min`, `volume_step`, `volume_max` rejected
- `volume_min > volume_max` rejected
- `volume_step > volume_max` rejected
- Error message includes symbol name
- Engine raises `ValueError` with message on invalid metadata

---

## Execution Assumptions (Documented)

| Assumption | Detail |
|---|---|
| Candle data side | Bid-based OHLC |
| BUY entry | ask = bid_open + (spread + slippage) × point |
| SELL entry | bid = bid_open − slippage × point |
| BUY exit | bid (SL/TP levels in bid terms) |
| SELL exit | ask = bid_exit + spread × point |
| Fill timing | Next M5 candle after the signal candle |
| Intrabar SL/TP | SL wins ambiguous bars (conservative) |
| Entry-candle | Evaluated for SL/TP; gap check omitted at open |
| Commission | Per-lot per-side; round-trip at close time |
| Swap | Excluded; noted in `cost_note` |
| Daily baseline | UTC calendar day boundary |
| Daily guard | Blocks new entries; does not force-close open positions |
| Floating mark (BUY) | bid_close |
| Floating mark (SELL) | ask_close = bid_close + spread |
| Force-close | At last candle close (bid for BUY, ask for SELL) |

---

## Known Limitations

- **Swap excluded.** Overnight financing costs are not modelled.
- **Fixed spread.** A single configurable spread_points is used; real-market spread is dynamic.
- **No partial fills.** Volume is sized and filled as one block.
- **No tick data.** Intrabar SL/TP order is unknown on ambiguous bars; SL-first is used as a worst-case assumption.
- **Daily baseline in UTC.** Some brokers reset at a different server time.
- **Commission is round-trip at close.** Entry commission is not deducted from the daily loss guard until the trade closes.

---

## Files Changed

| File | Fixes |
|---|---|
| `backtest/execution.py` | F1: added `compute_fill_price()` |
| `backtest/positions.py` | F2: `check_entry_candle()`; F3: SELL ask-side; F4: `commission_usd`; F5: `unrealized_pnl()` |
| `backtest/ledger.py` | F4: `total_commission`, `net_pnl`, consecutive runs |
| `backtest/engine.py` | F1–F6: all fixes; `validate_symbol_meta()` |
| `cli.py` | F6: removed hardcoded fallback |
| `reporting/metrics.py` | F4: commission in report; disclaimer added |
| `tests/unit/test_execution.py` | F1: `compute_fill_price` tests |
| `tests/unit/test_positions.py` | F2, F3, F4: entry-candle, ask-side, commission |
| `tests/unit/test_ledger.py` | F4: commission stats; consecutive wins/losses |
| `tests/unit/test_engine.py` | F1–F6: integration tests |
| `tests/unit/test_symbol_meta.py` | F6: metadata validation (new file) |

---

## Test Results

```
platform win32 — Python 3.13.5, pytest-9.1.1
collected 197 items (unit); 4 integration items skipped (MT5 not running)

197 passed in 3.10s
```

**Pre-refinement:** 142 tests  
**Post-refinement:** 197 tests (+55 new regression tests)  
**Regressions introduced:** 0

---

## Phase Gate Status

| Gate | Status |
|---|---|
| G4a Monetary risk matches configured risk_pct | PASS — sizing uses actual fill price |
| G4b Entry-candle SL/TP evaluated | PASS — `check_entry_candle` on fill candle |
| G4c SELL exits on ask side | PASS — spread applied in `check_candle` |
| G4d Commission in net P&L | PASS — round-trip at close, `net_pnl_usd` |
| G4e Daily guard includes floating P&L | PASS — `unrealized_pnl` summed before guard |
| G4f No unsafe metadata fallback | PASS — `validate_symbol_meta` enforced |

**G4 cleared (refined). Ready to push and proceed to Phase 5.**

---

## Engineering Score

**Score: 83/100**

**What's solid:**
- All six correctness issues fully fixed with no regressions
- 55 targeted regression tests added, each testing a specific edge case
- Bid/ask execution model is internally consistent and documented
- Metadata validation is enforced at the engine boundary, not just CLI
- Commission correctly distinguishes gross vs net P&L throughout the stack
- Floating loss in daily guard uses accurate ask-side marking for SELL

**Remaining limitations (~17 points):**
- Swap is completely excluded (5 points)
- Fixed spread; no dynamic spread model (4 points)
- Commission deducted at close, not entry — slight daily guard inaccuracy (3 points)
- No tick-level data; intrabar execution order is an assumption (3 points)
- UTC daily boundary may not match broker server time (2 points)

Do not interpret backtest performance as guaranteed future profitability.
