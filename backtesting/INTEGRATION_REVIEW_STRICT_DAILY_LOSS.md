# Integration Review — Strict Daily-Loss Protection

**Base:** `main` at `65bec706b0e61184cc82563809f475c5ae9b1644`
**Review branch:** `integration/phase-4-consolidation`
**Status:** Review only. No production code has been merged.

## Baseline validation

The local checkout on 2026-11-15 reported 389/389 unit tests passing on main (Python 3.13.5, pytest 9.1.1). This is baseline evidence only; the GitHub connector cannot run the Python test suite.

## Strict daily-loss feature branch

Source branch: `feat/strict-daily-loss-protection`, head `50a656c6d2e3c67ca7a13b0fd3dec84010143588`.

The branch changes:
- `backtesting/src/mt5_liquidity/backtest/engine.py`: evaluates account risk before ordinary candle SL/TP bookkeeping; applies risk-specific CLOSE_ALL processing; adds a daily risk budget gate before fills.
- `backtesting/src/mt5_liquidity/risk/account_risk.py`: adds `daily_loss_remaining` and `check_daily_risk_budget`.
- Unit tests for budget calculations, oversized entry rejection, lock ordering, forced closes and ledger reconciliation.
- An implementation report documenting the intended scope and validation caveats.

## Code-review finding: aggregate open risk is not reserved

The daily admission check calculates remaining allowance from realised equity, floating P&L and the daily baseline. It then compares only the *new proposed trade's* initial-stop risk to that remaining amount. It does not reserve the initial-stop risk of positions already open.

Therefore multiple open positions can each pass admission while their combined planned stop risk exceeds the remaining daily loss allowance. The feature report's own wording also clarifies that it is not a hard realised-loss cap. Add an aggregate-risk reservation model (including costs and current exposure) or explicitly redesign/test the policy before accepting this as strict aggregate admission control.

## Validation status

The source branch's report records:
- An initial run: 380 passed, 14 failed; the call-site keyword-only timestamp defect was corrected in commit `f23bd239`.
- A subsequent run: 388 passed, 6 failed due to fixtures contradicting the new admission rule; fixture changes were committed as `0fe25faf`.
- The report states the final fixture correction has **not yet been rerun**, and no final integration-suite result is available.

Consequently, this change is **not validated for merge**. The 389 passing tests on current main do not validate this divergent feature branch.

## Other branches

- `feat/phase-4-strategy-evaluation`: local branch reports one commit ahead, but remote branch is behind current main and contributes no unique remote changes. Local-only commit `5389ab1` is reported as intratrade MAE/MFE; it was not found on the remote branch and is intentionally excluded. Push it to a separately named remote branch or provide the full commit SHA before reviewing.
- `fix/phase-4-1-risk-accounting`: remains excluded. It is substantially stale and overlaps engine/risk accounting. Reapply its timezone, commission-aware sizing/equity and gross/net PF changes selectively against current main; do not merge its old branch wholesale.

## Required before merge

1. Fetch current origin and switch to this review branch.
2. Run `python -m pytest tests/unit -v` and `python -m pytest tests/integration -v`.
3. Add tests that prove simultaneous open-position planned risks are aggregated/reserved and that reservations are released/adjusted on close, partial close, and day rollover.
4. Rerun the historical XAUUSD sample and inspect `summary.json`, `risk_events.csv`, and `trades.csv`.
5. Reconcile risk locks, blocked-entry counts, forced closures, ledger P&L and final equity.
6. Keep this strictly as a backtesting/demo-only change; no claim of guaranteed daily loss containment.

## Safety

No changes have been made to `main`. No branches or pull requests have been merged, closed or deleted.