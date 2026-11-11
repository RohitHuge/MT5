# Phase 3 Implementation Report — Account-Level Risk Manager

**Branch:** `main`
**Scope:** `/backtesting` only
**Date:** 2026-11-11
**Phase status:** Implemented and verified locally. All acceptance criteria in `IMPLEMENTATION_PLAN.md` section 3I were executed and passed.

## 1. What was implemented

An independent, deterministic account-level risk supervisor
(`risk/account_risk.py`) that gates every new entry and applies a configured
open-position policy when protection thresholds are reached. It has no MT5
imports, no strategy imports, and no implicit filesystem access, so it is
fully unit-testable and reusable for future strategies.

Protections implemented (all individually configurable, all optional):

| Rule | Config (USD / % forms) | Reset policy |
|---|---|---|
| Maximum daily loss | `daily_loss_usd` / `daily_loss_pct` | Automatic at the daily boundary (UTC default) |
| Daily profit target | `daily_target_usd` / `daily_target_pct` | Automatic at the daily boundary |
| Equity trailing protection | `equity_trail_giveback_usd` / `..._pct` of peak | Never automatic (manual reset only) |
| Maximum account drawdown | `max_drawdown_usd` / `max_drawdown_pct` | Never automatic (manual reset only) |
| Consecutive losses | `max_consecutive_losses` | Lock resets at the daily boundary; the counter persists until a non-losing trade |
| Maximum risk per trade | `max_risk_per_trade_usd` / `..._pct` | Trade-level rejection, never an account lock |
| Maximum open positions | existing `max_positions` (engine) | unchanged |
| Trading halt | `halt()` / `reset_manual_lock()` API, `MANUAL_HALT` state | Operator release only |

Typed risk states (plan 3B): `ACTIVE`, `DAILY_TARGET_REACHED`,
`DAILY_LOSS_REACHED`, `EQUITY_TRAILING_TRIGGERED`, `MAX_DRAWDOWN_TRIGGERED`,
`LOSS_STREAK_REACHED`, `MANUAL_HALT`. When several locks are active the
reported state follows the deterministic precedence MANUAL_HALT >
MAX_DRAWDOWN > EQUITY_TRAILING > DAILY_LOSS > DAILY_TARGET > LOSS_STREAK;
every crossed threshold is logged as its own event.

Open-position policy (requirement 6): `open_position_action` is
`STOP_NEW_ENTRIES` (default — positions continue to be managed, SL/TP never
removed) or `CLOSE_ALL` (positions force-closed at the decision candle's
executable exit price with commission, using the existing bid/ask and exit
slippage conventions, exit reasons `RISK_*`). Positions are never silently
abandoned under either policy.

## 2. Files created and modified

**Created**

| File | Purpose |
|---|---|
| `src/mt5_liquidity/risk/account_risk.py` | `AccountRiskManager`, `RiskState`, `RiskEvent`, `RiskDecision`, persistence (`snapshot_state` / `restore_state` / atomic `save_state` / `load_state`), `build_account_risk_manager` factory |
| `tests/unit/test_account_risk.py` | 57 manager unit tests |
| `tests/unit/test_engine_risk.py` | 13 engine-integration tests |
| `tests/unit/test_reporting_risk_export.py` | 6 exporter tests |
| `PHASE3_IMPLEMENTATION_REPORT.md` | This report |

**Modified**

| File | Change |
|---|---|
| `src/mt5_liquidity/config.py` | `AccountRiskConfig` dataclass + `validate_account_risk_config()`; embedded as `BacktestConfig.account_risk` (disabled by default) |
| `src/mt5_liquidity/backtest/engine.py` | Risk supervision after mark-to-market valuation and before every entry admission; CLOSE_ALL execution; risk counters and outputs on `BacktestResult` (all new fields defaulted — disabled behaviour is unchanged) |
| `src/mt5_liquidity/reporting/exporter.py` | Schema version 3: `risk_events.csv`, `risk` section in `summary.json` and `metadata.json`, completeness validation |
| `src/mt5_liquidity/reporting/metrics.py` | `ACCOUNT RISK (Phase 3)` console section |
| `src/mt5_liquidity/cli.py` | Optional `--account-risk` flag family (all thresholds, `--open-position-action`); config validation with exit code 1 |
| `IMPLEMENTATION_PLAN.md` | Phase 3 checklist updated after verification |

## 3. Configuration and validation

All limits live in `AccountRiskConfig` (also settable via CLI flags). Safe
defaults: everything disabled (`enabled=False`), `open_position_action="STOP_NEW_ENTRIES"`,
daily reset at UTC. `validate_account_risk_config()` rejects: non-positive
thresholds, percentages above 100, `max_consecutive_losses < 1`, unknown
IANA timezones, and unknown open-position actions — the CLI exits 1 with a
clear message. When both USD and percentage forms are set for one rule,
whichever threshold is crossed first triggers (the tighter one).

Continuity rule: when `account_risk.daily_loss_pct` is unset and the manager
is enabled, the legacy `BacktestConfig.max_daily_loss_pct` value is used, so
enabling the manager preserves the pre-Phase-3 daily-loss level. When the
manager is disabled the engine keeps its original inline guard and Phase 1/2
behaviour is bit-for-bit unchanged (verified by the full existing suite).

## 4. Semantics and formulas

- **Daily P&L** = (realised equity + floating P&L) − daily baseline; the
  baseline is the mark-to-market equity observed at the first evaluation of
  each day. Floating P&L is included by construction (requirement to protect
  against unrealised losses).
- **Trailing protection:** high-water mark tracks the highest observed
  mark-to-market equity; floor = peak − effective giveback; the floor is
  clamped monotonic (never falls); trigger at equity ≤ floor.
- **Maximum drawdown:** peak − mark-to-market equity ≥ limit (USD or % of
  peak). Suppressed as a duplicate once trailing has already triggered in
  the same direction? No — the reverse: once max drawdown triggers, further
  trailing events are suppressed to avoid contradictory duplicates (plan 3E).
- **Loss streak:** counts closed trades with net P&L < 0; a breakeven (0) or
  winning trade breaks it; the lock engages at the configured limit and
  clears at the next daily boundary (the counter itself persists).
- **Per-trade risk ceiling:** the sized trade's actual monetary risk
  (lots × SL points × value-per-point-per-lot) is compared against the
  ceiling before the fill; a violation skips the setup and records
  `MAX_RISK_PER_TRADE_USD`/`_PCT` but never locks the account.
- **Risk closures** are normal ledger trades (commission deducted, equity
  updated, Phase 2 analytics included) with exit reasons
  `RISK_DAILY_TARGET`, `RISK_DAILY_LOSS`, `RISK_EQUITY_TRAIL`,
  `RISK_MAX_DRAWDOWN`, `RISK_LOSS_STREAK`, `RISK_MANUAL_HALT` — clearly
  distinguishable from strategy `SL`/`TP` exits.

## 5. Engine integration points

1. Exits resolved (SL/TP) → each closed trade feeds `on_trade_closed`.
2. Mark-to-market valuation of the candle → `on_candle(...)` evaluates
   rollover, high-water mark, and every threshold — **before** any entry.
3. If a lock is active and policy is `CLOSE_ALL` → open positions are
   force-closed at the decision candle's executable exit price
   (spread + exit slippage included), booked with `RISK_*` reasons.
4. Every entry admission passes `evaluate_entry(...)`; a blocked setup is
   counted with its reason code (`setups_skipped_risk`,
   `risk_blocked_by_reason`).
5. After sizing, `check_trade_risk(...)` applies the per-trade ceiling.

A same-candle exit/new-entry sequence cannot bypass a fresh lock because the
lock is evaluated before the entry loop of the same candle (regression
tested).

## 6. Persistence (plan 3G)

`snapshot_state()` produces a versioned (`schema_version: 1`) dict with
active locks, daily baseline, current day, peak equity, floor, loss streak,
last processed timestamp, event history, and counters. `save_state(path)`
writes atomically (temporary file + `os.replace`). `restore_state()` /
`load_state()` fail safe with `RiskStateError` on: unreadable or corrupt
JSON, unsupported schema versions, configuration fingerprint mismatch
(symbol/initial equity/risk config hash), missing files, and any persisted
peak below the currently known peak (never silently weaken a protective
high-water mark). A manager restored while locked keeps blocking entries
(tested).

## 7. Test results

```
tests/unit         389 passed   (incl. 76 new Phase 3 tests)
tests/integration    4 passed
---------------------------
total              393 passed, 0 failed, 0 skipped  (pytest 9.1.1, Python 3.13.5)
```

New coverage highlights:
- Every rule: trigger, exact-boundary (at-threshold triggers), just-below
  stays active.
- Daily loss/target with floating P&L crossing; UTC and configurable
  (Asia/Tokyo) daily resets; open positions across rollover.
- Trailing: peak updates from floating profit, floor monotonicity, exact
  floor trigger, pullback trigger, lock surviving rollover, no duplicate
  events while locked.
- Max drawdown vs trailing precedence and duplicate suppression; account
  locks never auto-reset.
- Loss streak counting, breakeven break, lock reset with persistent counter.
- Multiple simultaneous triggers: all events recorded, highest-precedence
  state reported.
- Per-trade ceiling: USD, percentage, OR semantics, no lock on rejection.
- Persistence: round trip, save/load file, corrupt file, wrong schema,
  account mismatch, weakened peak rejection, missing file, atomic write,
  restart-while-locked.
- Engine integration: every trigger with open positions, CLOSE_ALL vs
  STOP_NEW_ENTRIES, same-candle bypass prevention, accounting
  reconciliation (ledger net P&L, final equity, final MTM point), disabled
  legacy path unchanged.
- Exporter: `risk_events.csv` columns, summary/metadata risk sections,
  schema version 3, RISK_* vs SL/TP distinction, no secrets.

## 8. Example backtest with triggered protections

Command (historical XAUUSD M5 sample 2026-08-13 → 2026-11-10, live demo
terminal, 17,353 candles):

```
python -m mt5_liquidity.cli backtest --symbol XAUUSD --from 2026-08-13 --to 2026-11-10 `
  --risk-pct 0.5 --rr-target 2.0 --max-positions 1 --max-daily-loss-pct 3.0 --equity 10000 `
  --account-risk --daily-target-pct 0.5 --daily-loss-pct 0.5 `
  --equity-trail-giveback-pct 1.0 --max-consecutive-losses 5 `
  --open-position-action CLOSE_ALL
```

Report: `reports/XAUUSD/2026-11-11_07-54-54_edced52a` (published to GitHub,
commit `ab409e0`). Observed behaviour:

1. **Daily loss protection** triggered 2026-08-13 16:45 UTC (observed loss
   $98.10 ≥ $50 threshold) — `DAILY_LOSS_TRIGGERED` with `CLOSE_ALL` action.
2. **Automatic UTC reset** on 2026-08-14 01:00 UTC cleared the daily lock
   (`DAY_RESET`, `cleared locks: ['DAILY_LOSS_REACHED']`).
3. **Equity trailing protection** triggered 2026-08-17 20:00 UTC when
   mark-to-market equity $9,895.01 fell to the $9,900 floor (1% giveback
   from the $10,000 peak) while a SELL was open; the position was
   force-closed (`RISK_EQUITY_TRAIL`, net −$6.89) — `FORCED_CLOSE` event
   lists the affected setup.
4. The **account-wide lock survived every subsequent UTC day reset** (all
   later `DAY_RESET` rows show `cleared locks: none`), and 27 later entries
   were blocked with reason `RISK_EQUITY_TRAIL`.
5. Final: 3 trades (vs 20 in the unprotected Phase 2 run), net −$104.99,
   max drawdown $104.99 (1.05%) vs $472.39 (4.72%) unprotected.

Interpretation guard-rail (plan requirement): the smaller drawdown comes
from **lower exposure** (fewer trades taken), not from better strategy
signal quality. This is not evidence of profitability and no strategy
parameter was tuned. Measuring exposure-vs-quality effects is explicitly a
Phase 4 comparison task.

## 9. Known limitations

1. Risk force-closes execute at the decision candle's close price (the same
   conservative OHLC limitation as end-of-series force-closes); intrabar
   trigger/close ordering is not observable from M5 bars.
2. Daily-reset `DAY_RESET` events are logged for every trading day, so
   `risk_events.csv` grows with the number of days; the log is
   straightforward to filter by `event_type`.
3. In a backtest, manual halt and manual lock resets can only be exercised
   through the API (no operator); both are covered by unit tests and are
   the persistence path for the future demo adapter.
4. Swap remains excluded from P&L (existing disclosed cost limitation).

## 10. Issues discovered during implementation

- Fixed during testing: `close_all_requested()` originally reported the
  CLOSE_ALL policy without checking whether any protection lock was active,
  which would force-close positions while `ACTIVE`. Corrected to require an
  active lock; regression covered by `TestOpenPositionPolicySignal` and the
  engine tests.
- Phase 1's remaining manual-audit items (focused spread/slippage/
  ambiguous-candle verification) are still open; Phase 3 did not change any
  execution code path except adding the risk gate.

## 11. Phase boundary confirmation

No Phase 4 work was implemented: no experiment runner, no scenario matrix,
no train/validation split. No strategy signal rule was modified and no
parameter was optimised. Phase 2 analytics and Phase 1 execution behaviour
are unchanged (full existing suite passes), and the risk manager is a
separate module the strategy never calls.
