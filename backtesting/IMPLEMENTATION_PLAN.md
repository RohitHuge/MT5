# Backtesting Improvements — Atomic Implementation Plan

**Scope:** `/backtesting` only  
**Repository:** `RohitHuge/MT5`  
**Target branch:** `main`  
**Status:** Phase 1 code committed; local verification partially complete (see `PHASE1_IMPLEMENTATION_REPORT.md`). Phase 2 implemented and verified on 2026-11-11 (see `PHASE2_IMPLEMENTATION_REPORT.md`). Phases 3–4 not started.  
**Purpose:** Correct historical execution first, then add time-based performance analytics, account-level profit protection, and controlled comparative experiments.

---

## Project-wide rules

- [ ] Keep strategy signal-generation logic separate from execution, account-risk policy, analytics, and report serialization.
- [ ] Preserve backward compatibility for existing CLI arguments and report files unless a schema change is explicitly versioned.
- [ ] Use UTC-aware timestamps internally. Convert to configured broker/session time only at clearly defined analytics boundaries.
- [ ] Use closed-trade net P&L for realised performance. Include floating P&L when computing live/equity-based protection.
- [ ] Record the reason for every rejected entry, risk trigger, forced exit, reset, and skipped experiment.
- [ ] Never use future candles or future equity values to make a historical decision.
- [ ] Do not tune strategy parameters and protection parameters on the same validation period.
- [ ] Do not describe a backtest or passing unit suite as proof of future profitability.
- [ ] Each implementation task must add or update tests. Keep the full existing unit suite passing.
- [ ] Keep generated reports under the existing report folder and retain best-effort GitHub publishing.
- [ ] Do not commit credentials, terminal login details, local candle caches, or other runtime secrets.

## Current code map

| Current path | Responsibility to preserve / extend |
|---|---|
| `src/mt5_liquidity/backtest/engine.py` | Chronological backtest loop, setup-to-fill mapping, position lifecycle, equity updates |
| `src/mt5_liquidity/backtest/positions.py` | Bid/ask-aware SL/TP checks, trade closure and P&L |
| `src/mt5_liquidity/backtest/execution.py` | Simulated entry/fill pricing |
| `src/mt5_liquidity/backtest/ledger.py` | Closed-trade ledger and aggregate statistics |
| `src/mt5_liquidity/risk/position_sizing.py` | Risk-based lot sizing |
| `src/mt5_liquidity/config.py` | Backtest configuration defaults and environment loading |
| `src/mt5_liquidity/reporting/exporter.py` | Immutable report bundle export |
| `src/mt5_liquidity/reporting/metrics.py` | Console metrics |
| `src/mt5_liquidity/reporting/github_publisher.py` | Best-effort report publication to GitHub |
| `src/mt5_liquidity/cli.py` | Backtest CLI wiring and report publication |
| `tests/unit/` | Existing unit/regression test suite |
| `tests/integration/` | End-to-end and cross-module tests |

---

# Phase 1 — Execution correctness and regression fixes

**Goal:** Ensure the simulator's trade records, position limits, prices, and risk sizing are internally consistent before building more analytics or protective rules.

## 1A. Reproduce and document report anomalies

- [ ] Add a regression fixture using the November 10, 2026 XAUUSD report's relevant trade rows and setup/candle identifiers.
- [ ] Reproduce profitable trades labelled `SL` and identify whether each is a legitimate stop moved beyond entry or an incorrect trigger/price/P&L result.
- [ ] Reproduce multiple entries sharing the same fill candle, including the `SETUP_0191` / `SETUP_0192` case.
- [ ] Reproduce multiple signals on one candle where the first trade is closed immediately on its entry candle.
- [ ] Reproduce the unusually large lot sizes caused by very small stop distances.
- [ ] Record the current expected versus observed behaviour in test names and comments; do not hardcode historical P&L as a desired outcome.

## 1B. Define chronological candle-processing semantics

- [ ] Specify the exact per-candle order of operations: mark existing positions, evaluate exits, update equity, process risk guards, consider new entries, and record end-of-candle state.
- [ ] Define whether a position closed on a candle may be replaced by another setup on that same candle. Select one deterministic policy and document it.
- [ ] Ensure a position opened during a candle cannot be evaluated using price movement that occurred before its simulated fill time.
- [ ] Ensure all setups sharing a fill candle are processed in deterministic chronological/tie-break order.
- [ ] Define duplicate-signal handling using stable setup identity and/or an explicit signal deduplication key.
- [ ] Ensure the configured maximum simultaneous position count is enforced at every point in the event loop.
- [ ] Add tests for two same-candle signals, immediate entry-candle exit followed by another signal, duplicate setup identity, and a position closing before a later-candle entry.
- [ ] Add an invariant assertion/test helper that observes open-position count and proves it never exceeds `max_positions`.

## 1C. Audit fill, spread, SL/TP, and P&L accounting

- [ ] Trace the BUY entry and exit price conventions from `execution.py` through `positions.py`.
- [ ] Trace the SELL entry and exit price conventions, including ask-side spread treatment.
- [ ] Verify that spread is charged exactly once per round trip for both BUY and SELL.
- [ ] Verify slippage is applied on entry consistently with the documented model.
- [ ] Verify normal SL, normal TP, gap-through-SL, and force-close execution prices.
- [ ] Verify entry-candle SL/TP checks only use post-fill movement; where M5 OHLC cannot establish intrabar ordering, keep a documented conservative assumption.
- [ ] Verify the both-SL-and-TP-touched-in-one-candle policy remains conservative and deterministic.
- [ ] Verify an SL exit can be profitable only when the stop price is actually on the profitable side of the entry after the correct bid/ask convention.
- [ ] Add focused BUY/SELL tests for positive and negative stop outcomes, spread, slippage, gaps, and ambiguous candles.
- [ ] Reconcile each trade's gross P&L, commission, net P&L, and equity delta.
- [ ] Reconcile the sum of closed-trade net P&L with final equity minus initial equity, accounting explicitly for any deposits/withdrawals if they are ever introduced.

## 1D. Add position-sizing safety constraints

- [ ] Review `position_sizing.py` calculations against the broker symbol metadata currently provided to the engine.
- [ ] Add configurable minimum stop distance in points; reject trades below it rather than allowing extreme volume from near-zero stops.
- [ ] Add configurable strategy-level maximum lots per trade, independently of the broker's maximum volume.
- [ ] Add configurable maximum aggregate lots/open exposure where supported by the single-/multi-position model.
- [ ] Validate all new limits as positive and internally consistent during config loading.
- [ ] Keep volume rounding aligned with broker `volume_step`; never round up in a way that exceeds the configured monetary risk or a cap.
- [ ] Include calculated risk USD, stop distance, requested lots, final lots, and rejection reason in sizing diagnostics.
- [ ] Add tests for too-small stop distance, max-lot cap, exposure cap, volume-step boundaries, and invalid configuration.
- [ ] Add a report field/count for rejections caused by each new safety rule.

## 1E. Validate daily-loss behaviour already in the engine

- [ ] Confirm how UTC day boundaries and daily starting-equity baselines are initialised.
- [ ] Confirm the current daily guard includes unrealised P&L from open positions at the decision timestamp.
- [ ] Explicitly document that current daily loss protection blocks new entries but does not automatically close existing positions.
- [ ] Add tests at the exact daily threshold, just below it, just above it, and across midnight UTC with an open position.
- [ ] Ensure a new UTC day resets only daily counters/baselines and does not reset account-wide equity peak or other persistent account state.

## 1F. Phase 1 acceptance and regression

- [x] Run all existing unit tests. (2026-11-11: 212 passed before Phase 2 changes; 310 passed after.)
- [x] Run all new Phase 1 unit tests. (Included in the suite above.)
- [ ] Run the November 10 historical period and inspect every previously suspicious trade.
- [ ] Confirm position-limit invariants and explain any remaining same-candle trade rows.
- [ ] Confirm reported SL/TP reasons agree with entry, stop, exit and P&L values.
- [ ] Save a before/after audit note and report bundle; do not overwrite prior immutable reports.
- [ ] Do not begin Phase 2 until the execution anomalies are either corrected or individually explained with evidence.

**Phase 1 deliverable:** A deterministic and regression-tested execution engine with validated price accounting and bounded position sizing.

---

# Phase 2 — Time-based performance analytics

**Goal:** Explain when the strategy makes or loses money: by trade duration, entry hour, trading session, calendar day, week, and month.

**Status:** Implemented and verified 2026-11-11 — see `PHASE2_IMPLEMENTATION_REPORT.md`.

## 2A. Define analytics data contracts

- [x] Review the existing `ClosedPosition`, `LedgerStats`, and `equity_curve` fields before adding duplicated data.
- [x] Define one analytics input contract based on validated closed trades, equity observations, run configuration, and timezone/session configuration.
- [x] Define whether each grouping attributes a trade to entry time, exit time, or both. Use entry time for entry-cohort performance and exit time for exit-flow analysis; label them distinctly.
- [x] Use UTC-aware timestamps for calculations and ISO-8601 UTC timestamps in exported files.
- [x] Add explicit configured timezone support for display/session classification; do not infer broker timezone from the machine timezone.
- [x] Define behaviour for empty periods, no trades, zero gross losses, and incomplete data.
- [x] Add a versioned analytics schema section to report metadata.

## 2B. Build reusable performance calculations

- [x] Implement shared aggregation helpers for trade count, winners, losers, win rate, gross profit, gross loss, net P&L, average win/loss, and profit factor.
- [x] Implement period opening/closing equity and period return using a clearly documented equity convention.
- [x] Implement maximum drawdown from a chronological equity series, including the peak timestamp, trough timestamp, recovery timestamp if present, and duration.
- [x] Implement profitable/losing/flat period counts and percentages.
- [x] Implement average and median trade duration.
- [x] Implement consecutive wins/losses at trade level and profitable/losing streaks at period level as separate metrics.
- [x] Add tests proving grouped sums reconcile to the trade ledger and that ordering does not affect aggregation outputs.

## 2C. Trade-duration analysis

- [x] Calculate each trade's holding duration from `fill_ts` and `close_ts`, not from a rounded bar count alone.
- [x] Create configurable duration buckets with defaults: 0–5m, 5–15m, 15–30m, 30–60m, 1–2h, and over 2h.
- [x] Define boundary inclusivity so a trade at exactly 15 minutes is assigned to one and only one bucket.
- [x] Calculate trade count, win rate, net P&L, average net P&L, profit factor, and average adverse/favourable excursion where the required intratrade data exists.
- [x] Do not fabricate MAE/MFE from only entry/exit prices; add the required bar-path observations first or label those metrics unavailable.
- [x] Export `analytics/duration_analysis.csv`.
- [x] Add tests for zero-duration entry-candle trades, exact bucket boundaries, long-running trades, and empty buckets.

## 2D. Hourly performance

- [x] Add entry-hour grouping (0–23) using UTC as the canonical output.
- [x] Add optional configured-timezone hour grouping for broker/local analysis.
- [x] Calculate trade count, net P&L, win rate, average trade, profit factor, and drawdown contribution per hour.
- [x] Define separately whether an exit-hour report is included; do not mix entry-hour and exit-hour attribution.
- [x] Export `analytics/hourly_performance.csv` with all 24 hours represented, including zero-trade hours.
- [x] Add tests for midnight boundaries, timezone conversion, UTC offset changes, and empty hours.

## 2E. Trading-session performance

- [x] Add explicit session configuration for Asia/Tokyo, London, New York, and London–New York overlap.
- [x] Make session windows timezone-aware and daylight-saving-aware.
- [x] Define overlap attribution so a trade in the overlap is not accidentally double-counted in the all-session total.
- [x] Either support multi-label session analysis with clearly non-additive totals or a mutually exclusive primary-session classification; document which output is used.
- [x] Export `analytics/session_performance.csv`.
- [x] Add tests covering daylight-saving transitions, session boundaries, overlap windows, and UTC/local conversions.

## 2F. Daily, weekly, and monthly performance

- [x] Implement daily trade P&L by entry date and, separately, realised P&L by exit date.
- [x] Implement daily equity open, close, high, low, return, trade count, and drawdown.
- [x] Implement week grouping with an explicitly chosen convention (ISO week recommended) and include the ISO year to avoid year-boundary ambiguity.
- [x] Implement monthly grouping by calendar month.
- [x] Include zero-trade calendar periods within the requested test range when reporting account/equity performance.
- [x] Treat a trade that crosses midnight/week/month boundaries consistently: attribute trade P&L by exit time for realised cash-flow reporting, and use mark-to-market equity for period risk reporting.
- [x] Export `analytics/daily_performance.csv`, `analytics/weekly_performance.csv`, and `analytics/monthly_performance.csv`.
- [x] Add tests for weekend gaps, month/year transitions, cross-midnight trades, and periods with no closed trades.

## 2G. Mark-to-market equity and drawdown analytics

- [x] Extend the simulation output to capture equity at each M5 decision point, including floating P&L of open positions.
- [x] Keep the existing closed-trade equity series for compatibility, but name/document it as closed-trade or realised-equity data.
- [x] Define mark-to-market pricing using the correct close-side price for BUY and SELL positions, including spread.
- [x] Include commissions consistently in realised equity and clearly identify any costs not modelled, including swap if still excluded.
- [x] Calculate account equity peak, trough, maximum drawdown, time underwater, and recovery.
- [x] Export a separate mark-to-market equity/drawdown file without silently changing the meaning of the existing `equity_curve.csv`.
- [x] Add tests for floating profit/loss, open positions at period ends, spread-side valuation, and peak/trough/recovery calculations.

## 2H. Integrate analytics into report export and CLI

- [x] Add the analytics package under `reporting/` without putting report aggregation logic inside strategy or execution modules.
- [x] Update `exporter.py` to create the analytics directory and write every defined CSV/JSON output.
- [x] Update report completeness validation to require the new files for the new schema version.
- [x] Preserve existing summary, trades, metadata, equity curve, and execution log outputs.
- [x] Update `metrics.py` with a concise analytics summary (best/worst hour, best/worst session, profitable days, duration bucket highlights).
- [x] Include timezone and attribution conventions in `metadata.json`.
- [x] Ensure report publishing continues to publish only the report run directory using the existing publisher.
- [x] Add exporter tests for complete analytics bundles, empty datasets, schema versioning, and no secrets in metadata.

## 2I. Phase 2 acceptance

- [x] Run all unit tests.
- [x] Run integration tests over the historical XAUUSD sample.
- [x] Reconcile daily/weekly/monthly realised P&L to total net P&L.
- [x] Reconcile duration/hour/session trade counts to the relevant closed-trade population (with explicitly documented multi-label overlap exceptions).
- [x] Verify all period reports use the intended timezone.
- [x] Verify mark-to-market maximum drawdown can differ from closed-trade drawdown and both are labelled correctly.
- [x] Publish a fresh immutable report and verify all analytics files exist on GitHub.

**Phase 2 deliverable:** A multi-timeframe analytics report that can explain trade duration, time-of-day, session, daily, weekly, monthly, and intratrade/equity behaviour.

---

# Phase 3 — Account-level risk manager and profit protection

**Goal:** Add a separate account supervisor that decides whether new trades are allowed and what action is required when account-level thresholds are reached.

## 3A. Define account-risk configuration

- [ ] Extend `BacktestConfig` with optional account-risk settings while preserving current defaults and existing CLI behaviour.
- [ ] Add enable/disable flags for daily target, daily loss, equity trailing protection, maximum drawdown, and consecutive-loss guard.
- [ ] Support monetary and percentage inputs where meaningful; reject ambiguous combinations or define precedence.
- [ ] Add configurable daily reset timezone and reset boundary.
- [ ] Add configurable open-position action: `CLOSE_ALL` or `STOP_NEW_ENTRIES`.
- [ ] Add configuration for whether a daily target/loss lock resets next day and whether an account-wide drawdown lock requires manual reset.
- [ ] Validate thresholds, percentages, giveback values, and reset policies.
- [ ] Document that example thresholds are experimental settings, not recommended/proven profitable values.
- [ ] Add config parsing and validation tests.

## 3B. Implement risk state and decision model

- [ ] Define typed account-risk states: `ACTIVE`, `DAILY_TARGET_REACHED`, `DAILY_LOSS_REACHED`, `EQUITY_TRAILING_TRIGGERED`, `MAX_DRAWDOWN_TRIGGERED`, `LOSS_STREAK_REACHED`, and `MANUAL_HALT`.
- [ ] Define a risk decision result containing allowed/blocked status, state, reason code, trigger value, observed value, timestamp, and required action.
- [ ] Keep the risk manager independent of MT5 APIs and filesystem persistence so it is deterministic and unit-testable.
- [ ] Define deterministic trigger precedence when more than one threshold is crossed on the same candle.
- [ ] Define state transition rules, including lock and reset transitions.
- [ ] Add transition tests for every state, duplicate triggers, and invalid transitions.

## 3C. Implement daily profit and loss controls

- [ ] Track daily starting balance/equity using the configured daily timezone and reset boundary.
- [ ] Choose and document whether daily profit/loss is measured from realised P&L, equity including floating P&L, or both; account protection should use equity where required.
- [ ] Trigger daily profit target on the configured realised/equity measure.
- [ ] Trigger daily loss limit on equity including floating loss, not just closed trades.
- [ ] Block all new entries after a daily lock.
- [ ] Apply configured open-position policy after a trigger.
- [ ] Ensure the next daily reset does not clear an account-wide trailing/max-drawdown lock.
- [ ] Add tests for exact target/limit boundaries, floating P&L crossing, day rollover, and open positions during rollover.

## 3D. Implement account equity trailing protection

- [ ] Track the highest observed account equity (high-water mark) chronologically.
- [ ] Calculate the trailing protection floor from configured fixed-USD or percentage giveback.
- [ ] Ensure the floor can only stay constant or rise; it must never fall after a new peak.
- [ ] Evaluate the trigger using mark-to-market equity on every simulation decision point.
- [ ] Trigger when equity is at or below the protection floor according to a documented boundary rule.
- [ ] Capture peak equity, floor, trigger equity, giveback amount, and trigger timestamp in risk events.
- [ ] Define behaviour when equity reaches a new peak while an open position is active.
- [ ] Add tests for peak updates, floor monotonicity, exact trigger, floating profit then pullback, and restart/state restoration.

## 3E. Implement maximum drawdown and loss-streak protection

- [ ] Define maximum drawdown as a percentage or monetary loss from the account high-water mark, separately from trailing giveback if both are enabled.
- [ ] Prevent duplicate/contradictory triggers between maximum drawdown and equity trailing protection.
- [ ] Track consecutive losing closed trades using net P&L after configured costs.
- [ ] Define treatment of zero-P&L trades in a loss streak (recommended: reset or break the streak; make explicit).
- [ ] Lock new entries when the configured losing-streak limit is reached.
- [ ] Add tests for win/loss/flat sequences, threshold boundaries, and overlapping trigger conditions.

## 3F. Integrate risk decisions with the backtest engine

- [ ] Instantiate one account-risk manager per backtest run.
- [ ] Evaluate account risk after mark-to-market valuation and before admitting every new setup.
- [ ] Do not allow a same-candle exit/new-entry sequence to bypass an already-triggered account lock.
- [ ] Implement `CLOSE_ALL` by simulating each open position's close at the next available executable price under the chosen bar model, spread, slippage, and commission assumptions.
- [ ] Document the OHLC limitation when exact intrabar trigger/close ordering cannot be known; use a conservative model and record it.
- [ ] Implement `STOP_NEW_ENTRIES` without accidentally removing existing SL/TP monitoring.
- [ ] Record each forced closure with a distinct exit reason such as `RISK_DAILY_TARGET`, `RISK_DAILY_LOSS`, `RISK_EQUITY_TRAIL`, `RISK_MAX_DRAWDOWN`, or `RISK_LOSS_STREAK`.
- [ ] Ensure risk-triggered closures count in net P&L, commissions, equity, and analytics.
- [ ] Add counters for blocked setups by risk reason and risk-triggered closures.
- [ ] Add engine integration tests for every trigger with zero, one, and multiple open positions.

## 3G. Persist and recover risk state

- [ ] Define a serializable risk-state schema with a version field.
- [ ] Persist daily baseline, account high-water mark, protection floor, state/lock, loss streak, last processed timestamp, and trigger history.
- [ ] Validate restored state against account identity/configuration in any future live adapter; never mix accounts.
- [ ] Make state writes atomic so a partial write cannot corrupt the latest known state.
- [ ] Define stale-state and missing-state handling; fail safe rather than silently resetting a protective high-water mark.
- [ ] Add tests for serialization, deserialization, schema migration, corrupt state, stale timestamps, and restart while locked.
- [ ] Keep secrets and account credentials out of persisted risk state and generated reports.

## 3H. Risk event reporting and operator visibility

- [ ] Add a structured risk-event model with timestamp, state transition, trigger, observed equity, peak, floor, action, and action result.
- [ ] Export `risk_events.csv` and a concise risk summary in `summary.json`.
- [ ] Include risk configuration and reset/timezone policy in `metadata.json`.
- [ ] Print daily locks, trailing-stop activations, loss-streak locks, and forced-close counts in the console report.
- [ ] Clearly distinguish a protection-triggered stop from a strategy SL/TP exit.
- [ ] Add report tests ensuring risk events reconcile with blocked entries, forced closures, and final equity.

## 3I. Phase 3 acceptance

- [ ] Run all unit and integration tests.
- [ ] Prove daily target and daily loss include the intended floating/realised P&L measure.
- [ ] Prove equity trailing floor is monotonic and trigger uses mark-to-market equity.
- [ ] Prove account-wide locks do not reset at daily rollover unless explicitly configured.
- [ ] Prove risk-triggered close prices include the configured execution costs.
- [ ] Prove no new setup can enter after a lock is active.
- [ ] Prove restart recovery does not lose peak/floor/lock state.
- [ ] Inspect a historical run where the account reaches a profit peak and subsequently falls; verify the trigger and final result from the event log.

**Phase 3 deliverable:** A deterministic, auditable account-risk manager that can protect accumulated equity in simulation and has a persistence contract suitable for later live integration.

---

# Phase 4 — Comparative experiments and validation

**Goal:** Compare baseline strategy behaviour against protective configurations without data leakage, and establish whether the controls improve risk-adjusted consistency.

## 4A. Experiment runner and configuration

- [ ] Define an experiment configuration containing a stable experiment ID, baseline strategy config, account-risk config, date ranges, symbol, costs, and timezone assumptions.
- [ ] Support multiple named configurations in one experiment run.
- [ ] Ensure every configuration starts from the same initial equity and same historical candle data.
- [ ] Ensure one experiment's state, ledger, risk state, or cached outputs cannot leak into another experiment.
- [ ] Add CLI support for running a named experiment matrix while keeping the existing single-backtest command unchanged.
- [ ] Validate duplicate experiment IDs and invalid configurations.
- [ ] Record source code version/commit, config hash, data range, and data quality metadata for reproducibility.

## 4B. Define baseline and protection scenarios

- [ ] Add baseline scenario with account-level profit protection disabled and existing daily loss behaviour documented.
- [ ] Add daily-target-only scenario.
- [ ] Add equity-trailing-only scenario.
- [ ] Add combined daily-target plus equity-trailing scenario.
- [ ] Add maximum-drawdown and loss-streak scenario.
- [ ] Keep all thresholds configurable; use example values only as initial test inputs.
- [ ] Record the exact differences between scenarios so comparisons are explainable.

## 4C. Compare performance and protection outcomes

- [ ] Compare final equity, net P&L, return, profit factor, win rate, maximum drawdown, and trade count.
- [ ] Compare daily/weekly/monthly profitable-period rates and worst period.
- [ ] Compare maximum giveback from peak equity.
- [ ] Compare number of risk triggers by reason.
- [ ] Compare number of positions forcibly closed and additional execution costs from those closures.
- [ ] Measure missed subsequent opportunities after protection locks as a diagnostic, without treating hypothetical missed profit as realised performance.
- [ ] Compare trade duration, hour, and session distributions across scenarios.
- [ ] Report whether any improvement comes from lower exposure/trade count versus better strategy signal quality.
- [ ] Never select a scenario using final P&L alone.

## 4D. Train/validation separation and robustness

- [ ] Define chronological development, validation, and untouched forward-test date ranges.
- [ ] Tune thresholds only on the development period.
- [ ] Run selected configurations unchanged on validation period.
- [ ] Preserve the final test period as untouched until design choices are frozen.
- [ ] Run sensitivity analysis across several plausible profit-target, giveback, and loss-limit values.
- [ ] Test robustness across different market regimes and more than one historical window where data is available.
- [ ] Include realistic spread, slippage, commission, and swap assumptions where reliable historical inputs are available.
- [ ] Document limitations where costs or tick-level ordering are approximated.
- [ ] Avoid claiming statistical significance from a small number of trades or periods.

## 4E. Experiment report bundle

- [ ] Export one experiment-level `comparison.json` with all scenario configurations and headline metrics.
- [ ] Export `scenario_comparison.csv` with one row per scenario.
- [ ] Export daily/weekly/monthly comparisons and per-scenario risk event summaries.
- [ ] Link each scenario to its immutable individual backtest report directory.
- [ ] Include a concise recommendation section describing trade-offs, not just a winning configuration.
- [ ] Keep the existing report publisher flow and publish the experiment bundle without modifying prior reports.
- [ ] Add tests for deterministic experiment output, scenario isolation, report completeness, and no secret leakage.

## 4F. Final validation and release readiness

- [ ] Run the full unit test suite.
- [ ] Run all integration and regression tests.
- [ ] Run the complete experiment matrix on the same historical sample.
- [ ] Verify scenario results are reproducible from stored configuration and data metadata.
- [ ] Verify all analytics and risk-event totals reconcile to scenario ledgers.
- [ ] Review the validation-period results before accepting any protection configuration.
- [ ] Run a demo forward-test plan with live market data but demo execution only.
- [ ] Confirm risk-state recovery after process restart and simulated connectivity loss before considering live execution.
- [ ] Document unresolved modelling limitations and explicitly keep real-money trading out of scope until separately approved and validated.

**Phase 4 deliverable:** Reproducible, side-by-side backtest experiments that show whether account protection improves drawdown and consistency without hiding poor strategy performance.

---

## Cross-phase file change map

| Area | Expected paths |
|---|---|
| Execution fixes | `src/mt5_liquidity/backtest/engine.py`, `positions.py`, `execution.py` |
| Position sizing | `src/mt5_liquidity/risk/position_sizing.py`, `config.py` |
| Analytics | New modules under `src/mt5_liquidity/reporting/analytics/` |
| Account risk | New `src/mt5_liquidity/risk/account_risk.py`, `risk_state.py`, `risk_events.py` |
| Reporting | `src/mt5_liquidity/reporting/exporter.py`, `metrics.py` |
| CLI | `src/mt5_liquidity/cli.py` |
| Tests | Corresponding files under `tests/unit/` and `tests/integration/` |
| Documentation | This plan plus focused design/validation notes under `backtesting/docs/` |

## Definition of done

The work is complete only when:

- [ ] Phase 1 execution anomalies are explained or corrected with regression coverage.
- [ ] Phase 2 time-based metrics reconcile with the underlying ledger and have explicit timezone/attribution semantics.
- [ ] Phase 3 protection triggers are deterministic, cost-aware, auditable, and restart-safe.
- [ ] Phase 4 experiments are reproducible and use separate development/validation periods.
- [ ] All existing and newly added tests pass.
- [ ] Fresh report bundles contain the agreed analytics and risk-event files and publish successfully.
- [ ] No result is represented as guaranteed profit or as a prediction of future trading performance.

**Execution rule:** Complete and validate one phase before starting the next. Do not mark checklist items complete merely because code has been written; mark them complete only after their stated tests and acceptance checks have passed.
