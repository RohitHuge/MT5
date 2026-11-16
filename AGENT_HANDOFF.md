# MT5 Bot — Current Project State & Agent Handoff

**Last updated:** 2026-11-16  
**Repository:** https://github.com/RohitHuge/MT5  
**Default branch:** `main`  
**Purpose:** Give a newly added coding agent an accurate starting point. Read this file before making code changes.

> **Important scope distinction:** This repository contains two related but separate workstreams:
> 1. A live-connected Python MT5 demo-trading POC (`mt5-connectivity-poc/`), including manual and automatic demo-trading scripts.
> 2. A separate historical XAUUSD liquidity-sweep backtesting/risk-accounting system (`backtesting/`), with substantial work on unmerged branches.
>
> Do not assume that the backtesting engine is the execution engine used by `auto_trader.py`. Do not assume any feature on an unmerged branch is present on `main`.

---

## 1. What this project is

This is an automated trading proof of concept for a client using a **Vantage Markets MT5 demo account**. The current practical focus is the Python trading engine and proving that the bot behaves correctly in demo conditions.

The longer-term product architecture discussed in `prmpt.txt` is:

| Component | Intended responsibility | Status |
|---|---|---|
| Python | Trading engine, strategy, MT5 integration | Current focus / POC |
| MetaTrader 5 | Broker terminal and execution interface | Connected demo terminal |
| MQL5 | Possible future native MT5 execution bridge | Future / not established as implemented |
| React | Operator dashboard | Future |
| Node.js | API and WebSocket backend | Future |
| PostgreSQL | Trade history and analytics | Future |
| WhatsApp | Notifications and monitoring | Future |
| AI | Market analysis assistance | Future |

The above is a target architecture, not a statement that all components already exist.

## 2. Current state at a glance

### Connectivity POC

The user reports that **Phases 1–10 of `prmpt.txt` are complete**, with a final validation report of **16/16 PASS**. The setup included Python environment inspection, virtual environment, MetaTrader5 Python package, MT5 desktop terminal login, broker/account connectivity, symbol discovery, market data, and position discovery.

Reported local setup:
- Windows 11, 64-bit.
- Python 3.13.5, 64-bit.
- MetaTrader5 Python package installed in the project virtual environment.
- Broker: Vantage Markets.
- Correct demo server: `VantageMarkets-Demo` (not `VantageMarkets-Live 19`, which was an initial assumption).
- Instrument: `XAUUSD`.
- Reported XAUUSD contract size: 100 oz per standard lot.
- Minimum volume: 0.01 lot.
- Filling mode: IOC only (verify from current terminal symbol properties before relying on it).
- Reported spread observation: approximately $0.21 at the time measured; this is variable, not a guaranteed spread.
- Reported account mode: Retail Hedging.
- Reported leverage: 1:500.
- Reported balance at the time of the update: approximately $903. Balance is mutable and must be fetched from MT5, not treated as a constant.
- Account login and account-holder identity are deliberately omitted from this shared handoff. Do not place credentials or personal account identifiers in code, logs, commits, screenshots, or generated reports.

**Agent instruction:** Treat the above as user-provided setup facts. Revalidate current connection, account trade mode, server, symbol properties and permissions at runtime. Never infer that a terminal connected successfully means it is safe to place an order.

### Demo trading scripts

The user reports these scripts have been created and tested locally under `mt5-connectivity-poc/scripts/`:

| Script | Purpose | Reported state |
|---|---|---|
| `demo_trade.py` | Opens a manual XAUUSD BUY, displays live P&L, closes on Enter | Working; manually tested on demo |
| `auto_trader.py` | EMA bias-following automated demo bot | v3 written; v3 still needs a controlled test |
| `_cleanup.py` | Emergency utility to close open positions | Working; treat as high-impact and inspect before use |

The repo root also contains `details.json` with the user's local launch commands. The commands point to a Windows path and should be treated as machine-specific convenience instructions, not portable configuration.

### Current `auto_trader.py` iteration (user-reported; not independently source-verified in this handoff)

Three iterations have been tried:

1. **v1 — crossover-only:** waited for a fresh EMA cross event; did not trade during the observed run because the market was already trending.
2. **v2 — bias-following:** allowed an entry in the direction of the existing EMA bias; it traded, but the take-profit was too far away and stop-losses were hit.
3. **v3 — current candidate:** bias-following with a tighter take-profit and a cooldown after either SL or TP. **Not yet tested at the time of the user update.**

Reported v3 parameters:

| Parameter | Value |
|---|---:|
| Fast EMA | 3 |
| Slow EMA | 8 |
| Stop-loss | 80 points (reported as $0.80) |
| Take-profit | 60 points (reported as $0.60) |
| Check interval | 5 seconds |
| Cooldown | 2 cycles after SL/TP |

These are experimental settings, not validated or recommended trading parameters. Point-to-price conversion must be derived from live `symbol_info.point` and symbol digits, not assumed. The bot must verify the meaning of “cycle” in code (likely two polling iterations; confirm).

**Immediate next milestone:** Review v3 code, make sure order/position handling and cooldown logic are correct, then perform a tightly controlled demo-only validation. Do not start a long unattended session merely because v3 opens a trade.

---

## 3. Repository layout and source-of-truth notes

The current `main` root listing was verified on GitHub and contains:

- `README.md` (currently only a minimal placeholder)
- `prmpt.txt` — original connectivity POC specification / execution instructions
- `details.json` — local launch commands and brief operator instructions
- `mt5-connectivity-poc/` — connectivity and demo-trading workstream
- `backtesting/` — historical backtesting system and plans
- `docs/` — project documentation

Use the code on the checked-out branch as the authority for actual implementation. This handoff is context, not a substitute for inspecting source, tests, configuration, and Git history.

### Important local path reported by the user

```powershell
E:\PROJECTS\MT5\mt5-connectivity-poc
```

Reported launch commands (Windows; verify that the paths/files still exist):

```powershell
cd "E:\PROJECTS\MT5\mt5-connectivity-poc"
.\venv\Scripts\python.exe scripts\demo_trade.py
```

```powershell
cd "E:\PROJECTS\MT5\mt5-connectivity-poc"
.\venv\Scripts\python.exe scripts\auto_trader.py
```

The `details.json` in the repository provides the same operator commands.

---

## 4. Branch map — verified GitHub state on 2026-11-16

The repository currently has these five branches:

| Branch | Current purpose / contents | Relationship to `main` | PR |
|---|---|---|---|
| `main` | Default branch; report publication and baseline project | Current source of truth | — |
| `feat/phase-4-strategy-evaluation` | Strategy-evaluation feature work | GitHub compare reports 0 commits ahead and 2 behind; no unique diff against current `main` | No active PR surfaced in recent PR listing |
| `feat/strict-daily-loss-protection` | Strict daily-loss budget admission and engine integration | 11 commits ahead, 3 behind; diverged | PR #2, open, not merged |
| `fix/phase-4-1-risk-accounting` | Backtest risk accounting/reporting corrections | 13 commits ahead, 52 behind; heavily diverged | PR #1, open draft, not merged |
| `integration/phase-4-consolidation` | Consolidated risk-accounting, daily-loss and MAE/MFE work | 3 commits ahead, 1 behind; diverged from current `main` | PR #3, open draft, not merged |

**Important:** Do not merge branches based only on the “ahead” count. The branches diverged from different historical bases. Compare their full diffs against current `main`, inspect overlapping files, and preserve the report-only commit already on `main`.

### PR #1 — Phase 4.1 risk accounting

https://github.com/RohitHuge/MT5/pull/1

- Title: `Phase 4.1: risk accounting and backtest reporting corrections`
- State: open, draft, not merged.
- Branch: `fix/phase-4-1-risk-accounting`
- Includes changes to backtest engine, ledger, position handling, CLI, config, reporting metrics and tests, plus `docs/PHASE_4_1_IMPLEMENTATION_REPORT.md`.
- The PR description says this work adds a configurable IANA trading-day timezone, commission-aware floating equity and sizing, gross/net profit factor reporting, manual symbol metadata CLI support, and OHLC ambiguity documentation.
- PR description says tests were not executed by the GitHub connector authoring environment; require fresh local test execution before considering it validated.
- Branch is heavily behind/diverged from current `main`. Do not merge as-is.

### PR #2 — Strict daily-loss protection

https://github.com/RohitHuge/MT5/pull/2

- Title: `Merge strict daily-loss protection into main`
- State: open, non-draft, not merged; GitHub currently reports mergeable=false.
- Branch: `feat/strict-daily-loss-protection`
- 11 commits ahead / 3 behind current `main`.
- Its compared diff includes:
  - `backtesting/src/mt5_liquidity/backtest/engine.py`
  - `backtesting/src/mt5_liquidity/risk/account_risk.py`
  - `backtesting/tests/unit/test_account_risk.py`
  - `backtesting/tests/unit/test_engine_risk.py`
  - `backtesting/IMPLEMENTATION_PLAN.md`
  - `backtesting/STRICT_DAILY_LOSS_IMPLEMENTATION_REPORT.md`
- Contains strict daily loss budget admission and risk sequencing work.
- Do not merge without reconciling with PR #3 and current `main`.

### PR #3 — Consolidated backtesting risk and MAE/MFE

https://github.com/RohitHuge/MT5/pull/3

- Title: `Risk accounting, MAE/MFE and cost-model validation`
- State: open, draft, not merged; GitHub currently reports mergeable=true.
- Branch: `integration/phase-4-consolidation`
- Head commit: `072477173b1d326c204bc13f98e52961600a23d2`
- It includes 3 commits ahead of current `main` and is 1 commit behind.
- Main implementation areas include:
  - aggregate daily-risk admission and engine sequencing;
  - MAE/MFE tracking in USD and R;
  - export and analytics updates;
  - gross/net profit factor reporting;
  - tests including `test_mae_mfe.py`;
  - `backtesting/INTEGRATION_REVIEW_STRICT_DAILY_LOSS.md`.
- PR description reports the implementation run passed 417 unit tests + 4 integration tests = 421 total. These are local-run results recorded in the PR description, not a verified GitHub Actions status check.
- PR description records a historical XAUUSD replay with 14 trades, net P&L -$392.30, final equity $9,607.70, PF 0.3144, max drawdown $435.26, and 3 daily-loss forced closures. It is a historical backtest result, not a live demo-bot result.
- The PR explicitly says the $50 daily-loss limit is a trigger threshold, not a guaranteed hard maximum, because M5 bar sampling can detect the breach after the loss has already exceeded the threshold.
- It is the most consolidated backtesting branch, but still draft and not merged. Follow its merge gates in the PR description and review document.

### Report-only commit already on `main`

Commit:
`cc3672a10fb4067cd7c29d1794125b12252991f2`

This published a historical XAUUSD backtest report under:

`backtesting/reports/XAUUSD/2026-11-15_06-15-25_bfda217a`

It is a report publication, not the integration code merge. Do not delete or overwrite this immutable report.

---

## 5. Backtesting workstream — what exists and what is not complete

The `backtesting/` directory is a separate historical simulator for an XAUUSD liquidity-sweep strategy. Its strategy concept is:

1. Detect a liquidity sweep.
2. Wait for an M5 market-structure shift.
3. Wait for the first valid retest/rejection.
4. Enter on the next M5 candle.
5. Place SL beyond the sweep wick plus configured buffer.
6. Use a configured risk/reward target.

Historical data previously used: 17,353 M5 candles, with M15 aggregated from M5. Prior validation recorded 0 duplicate timestamps, 0 OHLC violations, 0 out-of-order candles, and 54 detected gaps. The source report identified 5 incomplete M15 windows.

The backtesting implementation plan in `backtesting/IMPLEMENTATION_PLAN.md` records:
- Phase 1 and Phase 2 implementation/validation as completed in the plan.
- Phase 3 account-level risk manager and profit protection as implemented and verified on 2026-11-11.
- Phase 4 comparative experiment runner and validation as **not complete**.

Phase 4 outstanding items include:
- deterministic experiment config and experiment matrix;
- multiple named scenarios run on identical data and initial equity;
- scenario comparison exports;
- chronological development / validation / untouched test splits;
- threshold sensitivity and market-regime robustness;
- realistic spread, slippage, commission and swap assumptions where reliable data exists;
- experiment-level report bundles and reproducibility;
- final demo forward-test and restart/connectivity-loss validation.

### Historical backtest result and limitations

The report on `main` at `backtesting/reports/XAUUSD/2026-11-15_06-15-25_bfda217a` reports:
- 14 trades; 2 winners and 12 losers (14.29% win rate).
- Net P&L: -$392.30; final equity: $9,607.70 from $10,000.
- Gross/net PF: 0.3144.
- Maximum drawdown: $435.26 (4.334%).
- 3 daily-loss forced closures.

Cost assumptions in that report:
- fixed spread: 21 points;
- entry slippage: 3 points;
- exit slippage: 0 points;
- commission: $0;
- swap: excluded.

Consequently, this is **not yet a full account-matched Vantage historical cost replay**. A fixed spread is a simplification; actual spread is variable. Applicable commission depends on account type/entity, and swap is direction-/account-/time-dependent. The demo terminal's actual XAUUSD specification and deal-level commission evidence are required before claiming broker-realistic costs. Do not substitute marketing minimum spreads or third-party swap snapshots.

### Daily-loss overshoot

The integration PR reports three forced closures beyond the configured $50 threshold:
- $56.67 loss on 2026-08-21;
- $56.05 loss on 2026-09-02;
- $51.48 loss on 2026-09-14.

The stated cause is that the simulator evaluates risk on M5 observations and cannot observe the precise intrabar tick at which the loss threshold was crossed. The threshold is a trigger, not a guaranteed realised-loss cap. Tick-level simulation or more granular data is needed to model this more precisely. This backtest limitation must not be confused with live account risk controls.

---

## 6. Immediate task list for the incoming agent

### Task 0 — establish a safe baseline

1. Check out `main` and confirm the working tree is clean. Preserve any local untracked files; do not remove or overwrite them.
2. Read this file, `prmpt.txt`, and the relevant `mt5-connectivity-poc/README.md` / source files.
3. Inspect the actual `auto_trader.py`, `demo_trade.py`, `_cleanup.py`, config files, requirements and current tests. Do not assume the user-reported v3 is exactly what is on GitHub.
4. Record `git status`, branch, commit SHA, Python version, installed package versions, MT5 terminal build, server, account trade mode and symbol metadata. Mask account identifiers.
5. Check that MT5 is in **DEMO** mode before any order-related test.

### Task 1 — validate auto_trader v3 (next milestone)

Review and test the v3 candidate under a controlled demo-only plan.

At minimum verify:
- it refuses to run if MT5 is disconnected or account is not demo;
- symbol is exactly the intended broker symbol and enabled in Market Watch;
- live symbol point/digits/tick size/contract size/volume min/step/max are read dynamically;
- IOC filling policy and supported order parameters are validated from the current symbol/account;
- volume and price rounding are correct;
- only the intended strategy position(s) are managed;
- it does not duplicate entries every polling cycle;
- EMA data uses closed candles or otherwise explicitly documents whether the current forming candle is used;
- bias logic and crossover logic are distinct and documented;
- SL and TP are placed/verified at broker side where supported;
- order result retcodes are checked, not merely the Python return value;
- cooldown starts only after a confirmed exit and is associated with the correct position/deal;
- restart behaviour does not accidentally create a second position;
- Ctrl+C performs safe shutdown without silently abandoning unmanaged positions;
- logs identify signal, order request, retcode, ticket, fill, SL/TP, exit reason and elapsed time without leaking account secrets.

Use a small, explicitly approved demo volume and a short supervised observation window. Do not change strategy thresholds or start/stop trading without making the change and its purpose explicit to the user. Do not claim profitability from a few demo trades.

### Task 2 — preserve operational safety

- Do not deploy to a live/real account.
- Do not request or store account passwords in code, logs, prompts or GitHub.
- Re-check `account.trade_mode` and `account.server` at runtime before any order path.
- Use an explicit allowlist for symbol and strategy-managed position identifiers/magic number.
- Add a maximum position count and loss/kill-switch handling appropriate for the demo test.
- Never blindly call `_cleanup.py`; inspect its exact selection criteria and show which positions would be closed. A global “close all positions” action can affect manual or unrelated positions.
- Any destructive operation must be confirmed by the operator.
- Never test against a real account.
- The backtest's $50 loss threshold is not evidence that the live bot has a $50 hard loss ceiling.

### Task 3 — record actual Vantage costs from the demo terminal

Use the logged-in MT5 desktop terminal:
1. Market Watch → right-click `XAUUSD` → Specification.
2. Record contract size, digits, point, tick size/value, volume min/step/max, execution/filling mode, swap mode, swap long, swap short and triple-swap day.
3. Identify account type and currency/entity if available.
4. Inspect demo deal history for actual commission fields/charges.
5. Capture spread from timestamped bid/ask ticks, not a single screenshot/point observation.

Do not hard-code current spread or swap as timeless constants. Keep account-specific settings in local ignored configuration where necessary and never commit secrets.

### Task 4 — decide how to proceed with Phase 4 backtesting

Do not merge the open PRs automatically. First compare the PR #3 branch against current `main`, reconcile its report-only commit ancestry and verify the PR's code with fresh tests. Use the integration branch as the likely consolidation reference, not as assumed truth.

Phase 4 experiments can be developed independently of frontend/backend work, but historical conclusions remain limited until costs are account-matched and validation/test windows are frozen.

---

## 7. What is explicitly future work

Not currently established as implemented:
- React operator dashboard.
- Node.js REST/WebSocket backend.
- PostgreSQL trade history service.
- WhatsApp alerts.
- AI market analysis assistance.
- MQL5 execution bridge.
- Production deployment / real-money trading.
- Broker-realistic historical spread + commission + swap replay.
- Completed Phase 4 comparative experiment runner and untouched out-of-sample validation.

Do not start the full frontend/backend architecture until the Python demo engine's requirements and event/data contracts are stable, unless the user explicitly changes priority.

---

## 8. Agent operating rules

1. **Inspect before editing.** Read the relevant source, config, tests and Git history.
2. **Do not conflate workstreams.** Live demo scripts and historical backtesting modules are separate unless code proves otherwise.
3. **Keep changes small and reviewable.** Use a new feature branch for material changes; do not commit directly to `main` unless explicitly instructed.
4. **Test every change.** Run focused tests first, then the relevant full suite. Report exact commands and results.
5. **No fabricated validation.** Clearly distinguish source code inspected, tests actually run, user-reported results and inferred behaviour.
6. **Protect the demo account.** Verify demo mode and account/server identity before any order submission. Never run live.
7. **No unapproved trades.** Trading tests must be explicitly scoped and supervised. A successful connection is not permission to trade.
8. **Do not make a bot appear successful by weakening assertions, suppressing errors, hiding losses or cherry-picking a date range.**
9. **Preserve reports.** Backtest report directories are immutable outputs. Write new runs to new run IDs.
10. **End every task with a handoff:** files changed, behaviour changed, tests, outstanding risks, branch/commit, and exact next action.

---

## 9. First response expected from the new agent

Before modifying code, the agent should return:

1. Current branch and commit SHA, plus clean/dirty working-tree status.
2. A short map of the files that implement `auto_trader.py`, manual demo trading, cleanup, MT5 connection and configuration.
3. Whether the checked-out `main` actually contains the user's described v3 settings and cooldown implementation.
4. Current test coverage and exact commands needed to run it.
5. A safe, step-by-step demo-only v3 validation plan.
6. Any mismatch between this handoff, the repository source and the live MT5 terminal.

**Do not place an order as part of this initial inspection.**
