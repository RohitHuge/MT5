# Strict Daily Loss Protection — Implementation Report

**Branch:** `feat/strict-daily-loss-protection`  
**Scope:** historical backtesting risk admission and OHLC decision-loop sequencing  
**Status:** Code changes committed to feature branch; local pytest and historical rerun still required. This is not a live-trading release.

## Summary

This change addresses two issues in the existing Phase 3 backtester:

1. The risk manager was evaluated only after normal SL/TP exits had been booked.
2. New trade admission checked the current lock but did not compare the proposed trade's stop risk with the remaining daily loss allowance.

The engine now evaluates daily/account risk before normal SL/TP bookkeeping on each M5 candle observation. For `CLOSE_ALL`, a triggered lock closes open positions at that candle's executable close and tags the closure as a risk exit. For `STOP_NEW_ENTRIES`, positions retain normal SL/TP management but new trades are locked.

The risk manager now exposes `daily_loss_remaining(...)` and `check_daily_risk_budget(...)`. Before an entry, the engine compares planned stop risk to the remaining daily loss allowance and rejects a trade that does not fit.

## What this does and does not guarantee

- It improves the OHLC backtest's sequencing and prevents admitting a trade whose planned stop risk exceeds the remaining daily loss budget.
- It evaluates realised plus floating P&L at each available M5 close.
- It does **not** guarantee realised loss will never exceed the configured USD limit. OHLC candles do not expose every intrabar price; gaps and slippage can move the market beyond the threshold before a close can be filled.
- The repository's engine, execution and strategy modules explicitly have no MT5 `order_send` path. The change does not implement live tick monitoring or broker order-close retries.
- A production MT5 adapter still needs tick/account-event monitoring, immediate entry lock, close-all order submission, verification of every position close, retry/error handling, durable account-bound state, and restart/reconnect fail-closed behaviour. It must run on demo first.

## Changed files

- `src/mt5_liquidity/risk/account_risk.py`: remaining budget calculation and pre-trade admission check.
- `src/mt5_liquidity/backtest/engine.py`: risk evaluation moved ahead of candle SL/TP booking; daily risk budget gate added before fills.
- `tests/unit/test_account_risk.py`: budget calculation, remaining-risk, threshold-fit tests.
- `tests/unit/test_engine_risk.py`: no-new-entry after daily breach, oversized daily-budget admission, same-candle lock ordering, forced-close behaviour, and ledger reconciliation tests. The daily-loss engine scenarios now use individually admissible positions (each planned stop risk fits the $50 allowance) and multiple concurrent positions to cross the account-level threshold; the protection logic was not weakened to accommodate the tests.

## Validation feedback and corrective commit

The first local unit-test run reported **380 passed, 14 failed**. All 14 failures shared one root cause: the engine passed the timestamp positionally to `check_daily_risk_budget`, while that method declares `ts` as keyword-only. This was a call-site defect, not 14 independent risk-rule failures.

Corrective commit: `f23bd239` — the engine now passes `ts=candle.timestamp`. The full unit suite must be rerun to verify this correction. The supplied console output ends immediately after the integration-test command, so no integration-test result is available yet.

## Follow-up test-fixture correction

A subsequent local run reported **388 passed, 6 failed**. The six failures were in engine tests whose fixtures attempted to open a single position with planned stop risk greater than the configured daily-loss allowance (or used a $1 daily allowance against a much larger minimum-lot risk). Those scenarios contradicted the new admission rule.

Commit: `0fe25faf` — the affected tests now use a 0.5% risk setting and a $50 daily limit. They create two positions whose individual planned risks fit the allowance, then move both into a combined floating loss that crosses the daily limit. The tests separately verify:

- `CLOSE_ALL` force-closes both open positions with a risk-specific exit reason.
- `STOP_NEW_ENTRIES` leaves existing positions under normal SL management.
- A later entry, including one mapped to the lock-trigger candle, is blocked after the combined loss triggers the lock.
- Risk-triggered closes are included in the trade ledger and final-equity reconciliation.
- The dedicated oversized-entry test still rejects a single proposed trade whose risk exceeds the remaining daily budget.

This is a test-fixture correction only; the admission and lock implementation was not changed in this follow-up. The updated tests have **not yet been rerun locally**.

## Validation still required

Run locally on the feature branch:

```powershell
cd E:\PROJECTS\MT5
git fetch origin
git switch feat/strict-daily-loss-protection
git pull origin feat/strict-daily-loss-protection
cd backtesting
pytest tests/unit -v
pytest tests/integration -v
```

Then rerun the historical XAUUSD sample with a fixed-dollar daily loss limit:

```powershell
python -m mt5_liquidity.cli backtest --symbol XAUUSD --from 2026-08-13 --to 2026-11-10 `
  --risk-pct 0.5 --rr-target 2.0 --max-positions 1 --max-daily-loss-pct 3.0 --equity 10000 `
  --account-risk --daily-loss-usd 50 --open-position-action CLOSE_ALL
```

Inspect `summary.json`, `risk_events.csv`, and `trades.csv`. Confirm that the daily lock is recorded before any subsequent setup is admitted and that the proposed-risk gate rejects entries whose initial-stop risk exceeds the remaining allowance. This backtest verifies candle-level logic, not hard live execution.

## Strategy loss diagnosis from the existing protected report

Report: `reports/XAUUSD/2026-11-11_08-43-13_5b7a196a`

- Executed: 3 trades; winners: 0; losers: 3; net P&L: -$104.99.
- Trade 1: BUY, -$49.60, normal SL, held 0 M5 bars (entry-candle exit).
- Trade 2: BUY, -$48.50, normal SL, held 0 M5 bars (entry-candle exit).
- Trade 3: SELL, -$6.89, closed by `RISK_EQUITY_TRAIL` after 2 M5 bars.
- The original unprotected report recorded 20 trades, 3 wins and 17 losses, net -$472.39, PF 0.3623. The protected run's 3 trades are a truncated sample after risk locks, not an independent strategy validation.
- The setup pipeline is liquidity sweep → M5 market-structure shift → first valid retest/rejection. Entry is next M5 candle; SL is beyond the sweep wick plus configured buffer; TP is fixed RR from actual fill.
- The two first trades entered and stopped on their fill candle. This is a high-priority diagnostic: entry-candle path ordering, spread/stop distance, signal-to-fill movement, and whether the stop placement is too tight relative to XAUUSD noise need analysis.
- The run is too small to attribute losses to a specific session, setup component or market regime. Do not infer strategy failure modes from only three protected trades.

## Other data and realism caveats

- Metadata reports five incomplete M15 aggregation windows in the sample.
- Spread assumption: 21 points; entry slippage: 3 points; exit slippage: 0; commission: $0; swap excluded.
- The model uses M5 OHLC and conservatively assumes SL before TP when both are touched. It cannot reconstruct exact tick sequence.
- Before selecting strategy parameters, Phase 4 should compare baseline versus controlled variants on identical data, use chronological development/validation splits, and add more realistic transaction-cost assumptions.

## Phase boundary

This is a backtest risk-engine enhancement only. It does not implement Phase 4, tune the signal strategy, or enable live execution. Do not treat it as a guarantee of a $50 maximum loss or as approval for real-money trading.
