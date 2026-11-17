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

---

## Consolidation implementation — 2026-11-15

**Status:** Implemented and locally validated on `integration/phase-4-consolidation`.

### Aggregate daily-risk admission

The integration now derives an admission reserve from every currently open
position at the time a setup is considered.  The daily mark-to-market result
already contains open floating P&L, so the reserve is only the additional loss
from each current executable mark to its executable initial stop.  The new
position contributes its complete executable fill-to-stop loss.  Round-trip
commission is included in both calculations when configured.

Admission succeeds only when:

```
daily loss limit + daily MTM P&L
    - sum(open mark-to-stop reserves)
    >= proposed initial-stop risk
```

This prevents both aggregate over-admission and double-counting a current
floating loss.  Reserves are never retained as mutable state: they are
re-derived from `open_positions` on every attempted entry, which releases them
on normal SL/TP exits, risk `CLOSE_ALL` exits, and end-of-run forced closes.
Invalid or missing reservation information fails closed.

Account-risk evaluation occurs before ordinary exit bookkeeping on each candle,
so a newly visible daily lock blocks a same-candle entry. `CLOSE_ALL` still
uses a risk-specific exit reason; `STOP_NEW_ENTRIES` continues ordinary
position management. A candle-level check cannot guarantee a hard realised
loss ceiling under gaps, slippage, or unknown intrabar order.

### Selective Phase 4.1 integration record

| Source commit | Result | Files / compatibility |
|---|---|---|
| `1c512db` | Already present | Configured `daily_reset_timezone` and timezone-aware reset tests existed on current baseline. |
| `6cc1029` | Reconciled | `engine.py`, `positions.py`: open MTM and sizing use estimated net floating equity; admission reserves include round-trip commission exactly once. |
| `cb19126`, `395eb4b`, `2e05b07` | Ported/reconciled | `ledger.py`, `metrics.py`, `exporter.py`, `test_ledger.py`: distinct gross and net profit factors are exported and displayed; legacy `profit_factor` remains the gross alias. |
| `45de3d8`, `aced962` | Already compatible | Existing position documentation and tests retain conservative SL-first OHLC ambiguity handling. |
| `f4a2820`, `b70da84`, `a065a4a`, `cfc2e4a` | Not ported | Current CLI requires retrieved, validated broker metadata rather than allowing a manual fallback; this is the stricter fail-safe behavior. |

### MAE/MFE recovery

Local commit `5389ab1ab9f048e76c9535038f4a8a532c1e5bc3` was available and
reviewed against parent `a50dfd7`. It was cherry-picked with provenance. Its
coverage verifies BUY/SELL executable-side excursions, entry candles, SL/TP,
risk closures, end-of-run closes, R multiples, exports, and aggregations. The
integration additionally records the full candle excursion before a risk
`CLOSE_ALL` exit.

### Fixture corrections

Several older engine-risk fixtures combined a $50 daily limit with a planned
$90–$100 stop loss. They were changed to use a $45 planned loss beneath the
$50 limit and a later $51 mark-to-market loss to exercise the intended lock.
This is required to test the new admission rule honestly; no assertion was
weakened.

### Verified runs

- Unit suite: `417 passed`.
- Integration suite: `4 passed`.
- Historical XAUUSD: `2026-08-13` through `2026-11-10`, `$10,000`, `0.5%`,
  `RR 2.0`, `$50`, `CLOSE_ALL`; report
  `reports/XAUUSD/2026-11-15_06-15-25_bfda217a`.

The historical replay had 14 closed trades, net P&L `-$392.30`, final equity
`$9,607.70`, gross/net PF `0.3144 / 0.3144` (commission was configured as
zero), three recorded risk closures, and five budget blocks plus five
daily-lock blocks. Ledger net P&L reconciles exactly to final equity.
