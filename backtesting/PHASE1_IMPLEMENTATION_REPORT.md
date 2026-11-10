# Phase 1 Implementation Report — Execution Correctness

**Branch:** `main`  
**Scope:** `/backtesting` only  
**Implementation status:** Code changes committed; local test execution and historical rerun pending.  
**Phase status:** Partially implemented — do not mark Phase 1 complete until the verification checklist below passes.

## Summary

Phase 1 changes address three concrete failure modes identified in the November 10 XAUUSD report:

1. Multiple setups could be admitted sequentially on the same M5 candle if an earlier setup immediately closed on its entry candle.
2. A gap could move the executable entry beyond the intended stop-loss. The engine sized using the absolute distance and could then record an SL at a price better than the fill, creating a profitable trade labelled SL.
3. Very small stop distances could mathematically produce unusually large lots. The previous strategy-level cap was only the broker's maximum volume.

The changes below make these cases explicit and add regression tests. They do not change the ICT signal-generation rules.

## Changes committed

| File | Change |
|---|---|
| `src/mt5_liquidity/config.py` | Added `min_stop_distance_points=5.0` and `max_lots_per_trade=1.0` safety defaults. |
| `src/mt5_liquidity/risk/position_sizing.py` | Added minimum stop-distance rejection, optional strategy-level lot cap, cap rounding down to broker volume step, and basic invalid metadata checks. |
| `src/mt5_liquidity/backtest/engine.py` | Deduplicates setup IDs; sorts same-candle candidates deterministically; enforces one successful entry attempt per M5 candle even if it immediately closes; rejects entries where the executable fill has crossed the intended stop. |
| `tests/unit/test_engine.py` | Added tests for same-candle immediate-exit replacement, duplicate setup IDs, and BUY/SELL gap-through-stop rejection. |
| `tests/unit/test_position_sizing.py` | Added tests for minimum stop distance, strategy lot cap, and rounding the cap down to volume step. |

## Behaviour decisions

### Same-candle policy

The simulator now admits at most one successful entry attempt per M5 candle. If that trade immediately hits SL/TP on its fill candle, later setups mapped to that same candle are still rejected. This is a conservative deterministic policy because M5 OHLC data does not reveal the intrabar ordering of independent signals.

The existing `max_positions` guard remains in place for simultaneous open positions.

### Gap-through-stop policy

Before sizing, the engine verifies stop direction against the executable fill:

- BUY: stop-loss must be below the executable fill.
- SELL: stop-loss must be above the executable fill.

If the fill has already crossed the stop, the trade is rejected as invalid instead of being opened and potentially booked as a profitable SL. The rejection is included in the existing sizing-rejection diagnostics.

This addresses an important possible cause of profitable SL-labelled trades. The historical report must still be rerun to determine whether every previously suspicious trade is eliminated or whether any remaining case is a legitimate stop behaviour requiring separate explanation.

### Position sizing safety defaults

- Minimum stop distance: 5 symbol points.
- Maximum strategy lot size: 1.00 lot per trade.
- The broker's own volume minimum, maximum, and step remain authoritative constraints too.
- When the strategy cap is not aligned to the broker volume step, the cap is rounded down, never up.
- If the resulting volume is below the broker minimum, the trade is rejected.

These are safety defaults for the current backtest configuration, not optimised trading parameters. They should be evaluated on historical and demo data before being treated as suitable for live use.

## Commits

- [Add safety configuration](https://github.com/RohitHuge/MT5/commit/dbc4440f86ce3b99545e1df837ea23ca85f77f40)
- [Add minimum stop-distance and lot cap sizing](https://github.com/RohitHuge/MT5/commit/4ec6cf723d3fb00c5c531cf7b980d98bdeecd016)
- [Enforce deterministic candle entries and reject gap-through stops](https://github.com/RohitHuge/MT5/commit/185a2bf6522e587681a1355d8eb28cdcebf540fa)
- [Add engine regression tests](https://github.com/RohitHuge/MT5/commit/11cc1abc1eb0a62a698daa6cb391bcb062423e5b)
- [Add sizing regression tests](https://github.com/RohitHuge/MT5/commit/76eb7af4d555c83c24feb96cb3e3b7ad2c6c47b3)

## Verification status

The GitHub files were fetched again from `main` after the updates and the new code/test blocks were confirmed present.

**Not yet verified:**
- The Python test suite has not been executed in the user's local Windows environment after these changes.
- The historical XAUUSD period has not been rerun after the changes.
- The previously suspicious trade rows have not yet been checked against the new report.
- The daily-loss baseline and floating P&L behaviour have not been changed in this patch; existing implementation blocks new entries but does not force-close open positions.
- Full spread/slippage/gap/ambiguous-candle accounting audit and complete P&L/equity reconciliation remain to be verified.

## Required local verification

From PowerShell:

```powershell
cd E:\PROJECTS\MT5
git pull origin main
cd backtesting
pytest tests/unit -v
```

Then rerun the historical sample:

```powershell
python -m mt5_liquidity.cli backtest `
  --symbol XAUUSD `
  --from 2026-08-13 `
  --to 2026-11-10 `
  --risk-pct 0.5 `
  --rr-target 2.0 `
  --max-positions 1 `
  --max-daily-loss-pct 3.0 `
  --equity 10000
```

Inspect the new `metadata.json` to confirm the 5-point minimum stop and 1.00-lot strategy cap are active. Inspect `trades.csv` for:

- No more than one admitted entry per fill candle.
- No entry whose fill is already beyond its stop.
- No unexpectedly oversized lots above the configured cap.
- Every SL-labelled trade has a stop on the loss side of the fill (unless a future, explicit stop-to-profit feature is added).

## Remaining Phase 1 work

- [ ] Run the complete local unit suite and resolve any failures.
- [ ] Run focused tests for BUY/SELL stop exits, gap-through SL, spread, slippage, force-close, and ambiguous SL+TP candles.
- [ ] Verify spread is charged exactly once for BUY and SELL round trips.
- [ ] Verify entry-candle OHLC handling and document the intrabar ordering limitation.
- [ ] Verify commission, net P&L, equity deltas, and final equity reconcile.
- [ ] Verify UTC daily baseline behaviour, including positions carried across a day boundary.
- [ ] Rerun the November 10 historical sample and audit all previously suspicious trades.
- [ ] Save and inspect a fresh immutable report bundle.
- [ ] Only then mark Phase 1 complete and proceed to Phase 2.

**Conclusion:** The core corrective code and regression tests are committed. Phase 1 is not declared complete because test execution and historical validation are essential acceptance requirements and have not been run from the local backtesting environment yet.
