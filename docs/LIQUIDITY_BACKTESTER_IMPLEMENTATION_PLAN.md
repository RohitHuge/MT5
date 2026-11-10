# XAUUSD Liquidity Sweep Backtester — Implementation Plan

> **Project:** MT5 Bot  
> **Document status:** Approved implementation plan  
> **Scope:** Phase-wise implementation of a Python terminal backtester for an M15 liquidity sweep + M5 market-structure strategy, followed by a separately gated MT5 demo-trading POC.  
> **Initial target environment:** Windows machine with the existing MetaTrader 5 desktop terminal installed.  
> **Instrument:** Broker-provided XAUUSD symbol (the exact symbol name must be discovered from MT5; brokers may use suffixes).  
> **Execution mode in this project:** Historical simulation only. No live or demo orders are to be placed by the backtester.

---

## 1. Goal and success criteria

Build a reproducible terminal application that retrieves historical market data from the already-installed MT5 terminal, identifies liquidity sweeps on M15, confirms entries on M5, simulates trades with realistic execution assumptions, and produces auditable performance reports.

The backtester is a research and validation tool. A profitable historical result is **not** proof of future profitability. The strategy must pass data-quality, correctness, out-of-sample, and demo-forward-testing gates before any claim about performance is made.

### Primary outcomes

- Fetch and validate historical XAUUSD M5 data through the existing MT5 Python integration.
- Construct or independently retrieve M15 data and verify that candle boundaries and OHLC values agree.
- Detect confirmed swing highs/lows, equal highs/lows, buy-side/sell-side liquidity and sweeps without look-ahead bias.
- Confirm a sweep using a fully specified M5 market-structure shift (MSS) and retest rule.
- Simulate entries, stop-losses, take-profits, position sizing, spread, commission and slippage.
- Produce deterministic trade logs, summary metrics and machine-readable exports.
- Keep the strategy engine independent from the CLI and future live execution adapter so the same strategy logic can be reused later.

### Explicit non-goals for the terminal-backtester phase

- No live trading.
- No demo-account order placement.
- No MQL5 Expert Advisor execution integration.
- No web dashboard, mobile app or WhatsApp bot.
- No AI/LLM deciding whether a trade should be placed.
- No claim or guarantee of profitability.
- No parameter optimization against the entire dataset.

---

## 2. Existing state and prerequisites

### Connectivity prerequisite — reported complete by project owner

The project owner has stated that the MT5 installation, Python package/connectivity checks and account connectivity tests have already passed.

The GitHub repository was inspected before this plan was added. At that time, the repository contained only a small `README.md`, one `main` branch and one initial commit; no connectivity-test source code or logs were present in the repository. Therefore:

- Treat MT5 connectivity as **previously completed according to the project owner**, not as independently verified from this repository.
- Do not repeat or replace the working connectivity setup unnecessarily.
- Before implementation, ask the project owner/coding agent to locate the existing local test script or capture its successful output.
- Record the terminal path, Python version, package version, broker server name and test timestamp without committing passwords, account credentials, investor passwords, API keys or other secrets.
- If the existing connectivity test fails in the implementation environment, stop and report the exact failure; do not silently mark the prerequisite complete.

### Prerequisite acceptance evidence

- [ ] Existing successful connectivity test location or output identified.
- [ ] MT5 terminal is open and logged in to the intended demo/historical-data account.
- [ ] Python can call MT5 initialization and retrieve terminal/account metadata.
- [ ] The account is used only for historical data during this phase.
- [ ] No credentials or sensitive account details are committed.

---

## 3. Strategy specification — Version 1

The first implementation must be deterministic and rule-based. Do not add discretionary interpretation or additional ICT concepts unless a later version explicitly approves them.

### 3.1 Timeframes and data

- Higher timeframe (HTF): M15.
- Entry/confirmation timeframe (LTF): M5.
- Primary symbol: broker's XAUUSD instrument.
- Strategy decisions are made only after the relevant candle has closed.
- Use broker/server timestamps as the canonical data timeline and explicitly record timezone handling.
- Prefer M5 source candles and construct M15 candles using aligned three-candle groups; if native M15 data is also retrieved, compare it with the constructed series and document any differences.

### 3.2 Swing detection

Implement confirmed pivot highs and pivot lows using configurable left/right lookback counts.

- A pivot high is confirmed only after the configured number of candles to its right have closed.
- A pivot low is confirmed only after the configured number of candles to its right have closed.
- A pivot must never be available to the strategy before its confirmation timestamp.
- Define deterministic handling for equal highs/lows and ties.
- Unit-test boundary conditions, flat candles, repeated highs/lows and insufficient history.

### 3.3 Liquidity zones

- Buy-side liquidity (BSL): zone around a confirmed swing high or a cluster of nearby/equal highs.
- Sell-side liquidity (SSL): zone around a confirmed swing low or a cluster of nearby/equal lows.
- Zone tolerance must be configurable in price units or points and interpreted using the broker symbol's tick size/digits.
- Store zone boundaries, source pivots, creation time, confirmation time and status.
- Define expiry/invalidation rules and avoid repeatedly trading the same consumed zone unless a new valid zone is formed.

### 3.4 M15 liquidity sweep

Initial SELL-side setup (sweeping buy-side liquidity):

1. A valid BSL zone exists and was known before the sweep candle began.
2. Price trades above the zone's upper boundary by the configured minimum sweep distance.
3. The completed M15 candle closes back below the relevant zone boundary.
4. Record sweep time, zone ID, sweep extreme and close-back-inside evidence.

Initial BUY-side setup (sweeping sell-side liquidity):

1. A valid SSL zone exists and was known before the sweep candle began.
2. Price trades below the zone's lower boundary by the configured minimum sweep distance.
3. The completed M15 candle closes back above the relevant zone boundary.
4. Record sweep time, zone ID, sweep extreme and close-back-inside evidence.

A wick beyond a level without the required close back inside is not a valid sweep in Version 1. Do not infer a sweep from an unfinished candle.

### 3.5 M5 market-structure shift (MSS)

After a valid M15 sweep, inspect only M5 candles whose close times are later than the sweep confirmation time.

- Bearish MSS: after a BSL sweep, a completed M5 candle closes below a previously confirmed M5 swing low.
- Bullish MSS: after an SSL sweep, a completed M5 candle closes above a previously confirmed M5 swing high.
- The broken swing must have been confirmed and known before the break candle.
- Define a configurable maximum number of M5 candles/time allowed for MSS confirmation.
- Expire the setup if no MSS occurs within that window.
- Log the exact broken swing, break candle, confirmation timestamp and price.

### 3.6 Retest and entry

Version 1 requires a retest after MSS; it must not enter retrospectively at the MSS candle.

- Bearish setup: after bearish MSS, price revisits the broken swing-low level or configured retest band from below and a completed M5 candle confirms bearish rejection.
- Bullish setup: after bullish MSS, price revisits the broken swing-high level or configured retest band from above and a completed M5 candle confirms bullish rejection.
- Define rejection-candle criteria explicitly (for example, candle direction and close location); make thresholds configurable and unit-tested.
- Entry occurs no earlier than the next executable price after the confirmation candle closes.
- If price never retests before setup expiry, record a missed/expired setup, not a trade.
- Do not use the confirmation candle's close as an executable fill unless the simulation explicitly models a market order submitted at that close and the next available executable quote supports it.

### 3.7 Initial stop-loss and take-profit

- SELL stop-loss: above the recorded sweep high plus a configurable buffer.
- BUY stop-loss: below the recorded sweep low minus a configurable buffer.
- Validate that stop distance is positive and meets broker minimum stop-distance rules where applicable.
- Initial target: 2R, where R is the entry-to-stop distance, configurable for experiments.
- Risk/reward is based on executable entry and exit prices, not candle mid-prices.
- Reject trades with invalid stop/target geometry or position size below broker minimum volume.

### 3.8 Setup lifecycle

Use explicit states, for example:

`ZONE_ACTIVE -> SWEEP_CONFIRMED -> MSS_CONFIRMED -> RETEST_PENDING -> ENTRY_FILLED -> CLOSED`

Terminal states also include `EXPIRED`, `INVALIDATED`, `REJECTED` and `MISSED`.

Every state transition must include timestamp, reason and supporting candle/zone identifiers. A setup can produce at most one entry in Version 1.

---

## 4. Proposed architecture

Keep pure strategy logic independent from MT5 I/O, terminal presentation and future order execution.

```text
MT5 Terminal (historical data only)
              |
              v
       MT5 Data Adapter
              |
              v
    Data Validation / Cache
              |
              v
      Candle Normalization
        |             |
        v             v
       M5          M15 (derived/verified)
        |             |
        +------v------+
               |
       Liquidity Strategy
   (swings -> zones -> sweeps)
               |
               v
       M5 Confirmation Engine
          (MSS -> retest)
               |
               v
       Backtest / Fill Simulator
               |
          Risk Sizing
               |
               v
       Trade Ledger / Metrics
               |
        CLI + CSV + JSON
```

### Separation rules

- `data`: MT5 connection, history retrieval, normalization and persistence only.
- `strategy`: deterministic signal logic; no MT5 API calls and no printing.
- `backtest`: event replay, order/fill simulation, position lifecycle and costs.
- `risk`: sizing and exposure constraints; no signal generation.
- `reporting`: metrics and exports.
- `cli`: user input, progress, formatted output and exit codes.
- `tests`: unit, integration, regression and end-to-end fixtures.

Future demo execution must be a separate adapter. Never let the backtest engine call a live/demo order method.

---

## 5. Suggested repository structure

Adapt to existing repository conventions if code is added later; do not create this tree merely as part of adding this plan.

```text
MT5/
├── README.md
├── docs/
│   └── LIQUIDITY_BACKTESTER_IMPLEMENTATION_PLAN.md
├── pyproject.toml                 # or requirements.txt; choose one dependency strategy
├── .env.example                   # placeholders only; never commit real credentials
├── .gitignore
├── src/
│   └── mt5_liquidity/
│       ├── __init__.py
│       ├── config.py
│       ├── cli.py
│       ├── data/
│       │   ├── mt5_adapter.py
│       │   ├── history.py
│       │   ├── candles.py
│       │   ├── validation.py
│       │   └── cache.py
│       ├── strategy/
│       │   ├── models.py
│       │   ├── swings.py
│       │   ├── liquidity_zones.py
│       │   ├── sweeps.py
│       │   ├── market_structure.py
│       │   ├── retests.py
│       │   └── liquidity_sweep.py
│       ├── backtest/
│       │   ├── engine.py
│       │   ├── execution.py
│       │   ├── positions.py
│       │   └── ledger.py
│       ├── risk/
│       │   └── position_sizing.py
│       └── reporting/
│           ├── metrics.py
│           ├── console.py
│           └── exports.py
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── fixtures/
│   └── regression/
├── data/
│   ├── raw/                        # ignored by git
│   └── processed/                  # ignored by git
├── reports/                        # ignored by git except .gitkeep if needed
└── logs/                            # ignored by git
```

---

## 6. Phase-wise implementation plan and atomic tasks

Each task must be completed, tested and reported before being marked done. Do not mark a task complete merely because code was written. Where a task depends on a broker-specific fact, inspect MT5 metadata rather than hardcoding assumptions.

### Phase 0 — Preserve and verify existing connectivity prerequisite

**Status:** Reported complete by project owner; evidence not present in repository at planning time.

- [ ] P0.1 Locate the already-passing MT5 connectivity test script or its execution output.
- [ ] P0.2 Record Python version and operating system.
- [ ] P0.3 Record installed MetaTrader5 Python package version.
- [ ] P0.4 Record MT5 terminal build/version and terminal executable path (do not expose private machine paths unnecessarily in public logs).
- [ ] P0.5 Record broker server label and account trade mode without recording login credentials.
- [ ] P0.6 Run the existing read-only connectivity test once in the target environment.
- [ ] P0.7 Verify terminal/account metadata can be read.
- [ ] P0.8 Verify a small historical rates request succeeds.
- [ ] P0.9 Store a sanitized test report and timestamp.
- [ ] P0.10 Confirm no order-send function is called by any connectivity test.

**Acceptance gate:** Existing test passes in the implementation environment, terminal/account metadata is readable, and a read-only rates request succeeds. If already-passed evidence is available and environment is unchanged, preserve it rather than unnecessarily rebuilding the connection layer.

### Phase 1 — Project scaffold and historical data ingestion

- [ ] P1.1 Inspect repository state and retain any existing files/conventions.
- [ ] P1.2 Create the Python package and chosen dependency configuration.
- [ ] P1.3 Add pinned or bounded dependencies and document supported Python version.
- [ ] P1.4 Add configuration loading with safe defaults.
- [ ] P1.5 Add structured logging and actionable error messages.
- [ ] P1.6 Add CLI command to list/search broker symbols.
- [ ] P1.7 Resolve exact XAUUSD symbol name from MT5 (including broker suffix/prefix).
- [ ] P1.8 Read symbol metadata: digits, point, tick size, tick value, contract size, volume min/max/step, stops level, trade mode and calculation mode where exposed.
- [ ] P1.9 Implement historical M5 rates retrieval with date range and pagination/chunking.
- [ ] P1.10 Handle MT5 history availability limits and report requested versus returned range.
- [ ] P1.11 Normalize rates to a typed internal candle model.
- [ ] P1.12 Validate chronological ordering.
- [ ] P1.13 Detect duplicate timestamps.
- [ ] P1.14 Detect missing intervals while accounting for weekends/market closures.
- [ ] P1.15 Validate OHLC invariants: high >= max(open, close), low <= min(open, close), high >= low.
- [ ] P1.16 Preserve tick volume, real volume and spread fields when supplied; document their meaning/availability.
- [ ] P1.17 Normalize timezone consistently; store timestamps in UTC internally and retain source timezone metadata.
- [ ] P1.18 Construct M15 candles from aligned M5 candles.
- [ ] P1.19 Verify M15 OHLC aggregation: first open, max high, min low, last close.
- [ ] P1.20 Compare derived M15 with MT5 native M15 history over an overlapping range.
- [ ] P1.21 Add local cache in a documented format (Parquet preferred if dependency is acceptable; otherwise CSV).
- [ ] P1.22 Add cache metadata including symbol, timeframe, date range, source and retrieval timestamp.
- [ ] P1.23 Add safe cache invalidation/refresh behavior.
- [ ] P1.24 Add unit tests for candle validation and timeframe aggregation.
- [ ] P1.25 Add integration test for a small known MT5 history range.
- [ ] P1.26 Add CLI command to fetch, validate and summarize data.

**Acceptance gate:** A user can request a date range, retrieve M5 data, derive/verify M15 data, cache it, and receive a clear data-quality report. Invalid or incomplete data must be surfaced, not silently repaired.

### Phase 2 — Swing points and liquidity zones

- [ ] P2.1 Define typed models for candles, pivots, liquidity zones and strategy events.
- [ ] P2.2 Define configurable left/right pivot lookback.
- [ ] P2.3 Implement confirmed swing-high detection.
- [ ] P2.4 Implement confirmed swing-low detection.
- [ ] P2.5 Ensure pivot availability begins only at its confirmation candle close.
- [ ] P2.6 Define deterministic handling of tied/flat highs and lows.
- [ ] P2.7 Implement equal-high clustering with configurable tolerance.
- [ ] P2.8 Implement equal-low clustering with configurable tolerance.
- [ ] P2.9 Implement BSL zone creation.
- [ ] P2.10 Implement SSL zone creation.
- [ ] P2.11 Store source pivot IDs and zone provenance.
- [ ] P2.12 Implement zone activation, consumption, invalidation and expiry.
- [ ] P2.13 Prevent duplicate zones from the same pivot cluster.
- [ ] P2.14 Add event logging with candle timestamps and prices.
- [ ] P2.15 Create synthetic candle fixtures with known swing points.
- [ ] P2.16 Unit-test pivots, ties, tolerance, confirmation delay and zone lifecycle.
- [ ] P2.17 Add a diagnostic command/report listing detected zones for a selected range.

**Acceptance gate:** On hand-crafted fixtures, swing points and zones match expected values and never appear before they are knowable.

### Phase 3 — Liquidity sweep and M5 confirmation

- [ ] P3.1 Finalize configurable minimum sweep distance and close-back-inside rule.
- [ ] P3.2 Implement BSL sweep detection for SELL setups.
- [ ] P3.3 Implement SSL sweep detection for BUY setups.
- [ ] P3.4 Store sweep candle, zone ID, sweep extreme and confirmation time.
- [ ] P3.5 Ensure sweep is evaluated only after the M15 candle closes.
- [ ] P3.6 Define M5 swing confirmation lookback independently from M15.
- [ ] P3.7 Implement bearish M5 MSS after BSL sweep.
- [ ] P3.8 Implement bullish M5 MSS after SSL sweep.
- [ ] P3.9 Ensure broken M5 swing was confirmed before the break.
- [ ] P3.10 Implement configurable MSS timeout/expiry.
- [ ] P3.11 Define retest band around broken structure level.
- [ ] P3.12 Implement bearish retest and rejection confirmation.
- [ ] P3.13 Implement bullish retest and rejection confirmation.
- [ ] P3.14 Ensure entry confirmation candle is closed before a signal is emitted.
- [ ] P3.15 Define setup state machine and permitted transitions.
- [ ] P3.16 Ensure one setup creates at most one trade.
- [ ] P3.17 Add rejection reasons for invalid/expired setups.
- [ ] P3.18 Add synthetic fixtures for valid BUY and SELL sequences.
- [ ] P3.19 Add fixtures for false sweep, no MSS, no retest, late retest and invalidation.
- [ ] P3.20 Add regression tests proving no future candle is read by earlier decisions.

**Acceptance gate:** Deterministic fixture tests prove correct BUY/SELL signals, no signals for invalid setups, and no look-ahead leakage.

### Phase 4 — Backtest simulation, risk and costs

- [ ] P4.1 Define the event-driven replay contract and candle ordering.
- [ ] P4.2 Replay candles chronologically without exposing future bars.
- [ ] P4.3 Define order submission timing after signal confirmation.
- [ ] P4.4 Model executable bid/ask prices using available tick data where possible.
- [ ] P4.5 If tick data is unavailable, define and disclose a conservative candle-based fill approximation.
- [ ] P4.6 Implement market-entry fill simulation on the next available executable price.
- [ ] P4.7 Implement initial SL/TP placement and validation.
- [ ] P4.8 Implement position sizing from account equity, risk percentage and actual symbol tick/contract metadata.
- [ ] P4.9 Handle volume min/max/step and reject invalid volume.
- [ ] P4.10 Include spread in entry/exit execution.
- [ ] P4.11 Include commission using configurable broker/account schedule.
- [ ] P4.12 Include configurable slippage model and disclose its assumptions.
- [ ] P4.13 Include swap/financing for positions held across applicable rollover periods if the data/model supports it; otherwise clearly report it as excluded.
- [ ] P4.14 Handle gaps and stop fills at the next available executable price rather than assuming an impossible fill at the stop.
- [ ] P4.15 Resolve bars where both SL and TP are touched using tick data; if unavailable, apply a documented conservative assumption (SL first).
- [ ] P4.16 Track open, closed and rejected positions.
- [ ] P4.17 Prevent unintended overlapping trades and enforce configurable maximum positions.
- [ ] P4.18 Implement configurable risk per trade and maximum daily loss guard for simulation.
- [ ] P4.19 Implement trade ledger with signal evidence, fill assumptions, costs and exit reason.
- [ ] P4.20 Add unit tests for long/short P&L, sizing, costs, gaps and ambiguous bars.
- [ ] P4.21 Add deterministic replay test with manually calculated expected trades.
- [ ] P4.22 Add no-trade behavior for invalid symbol metadata or missing cost configuration.

**Acceptance gate:** Hand-calculated scenarios match simulator output. Every trade can be traced from source candles through signal, fill, risk, costs and exit.

### Phase 5 — Terminal application and exports

- [ ] P5.1 Define CLI command structure and help text.
- [ ] P5.2 Add command to inspect terminal connection and symbol metadata (read-only).
- [ ] P5.3 Add command to fetch/cache historical data.
- [ ] P5.4 Add command to run a backtest against cached or freshly retrieved data.
- [ ] P5.5 Add arguments for symbol and date range.
- [ ] P5.6 Add arguments for strategy parameters.
- [ ] P5.7 Add arguments for starting balance and risk percentage.
- [ ] P5.8 Add arguments for spread, commission and slippage assumptions.
- [ ] P5.9 Add clear progress indicators for long-running fetch/replay/report stages.
- [ ] P5.10 Add cancellation handling and safe cleanup.
- [ ] P5.11 Add readable signal-decision output with the reasons for acceptance/rejection.
- [ ] P5.12 Add summary table and trade list.
- [ ] P5.13 Add CSV trade export.
- [ ] P5.14 Add JSON run-summary export.
- [ ] P5.15 Add run configuration and data provenance to each report.
- [ ] P5.16 Add stable exit codes for success, invalid input, data failure and test failure.
- [ ] P5.17 Add actionable error messages without printing secrets.
- [ ] P5.18 Add CLI integration tests using fixtures/mocked MT5 adapter.
- [ ] P5.19 Document setup and example commands in README after implementation.

**Acceptance gate:** A clean environment can install the project, inspect connectivity, fetch data, run a backtest and generate reports using documented commands.

### Phase 6 — Strategy validation and robustness

- [ ] P6.1 Verify a sample of detected pivots and sweeps manually against chart data.
- [ ] P6.2 Verify every reported trade against candle/tick evidence.
- [ ] P6.3 Reconcile gross and net P&L calculations.
- [ ] P6.4 Report total trades, wins, losses and breakeven trades.
- [ ] P6.5 Report win rate and average win/loss.
- [ ] P6.6 Report gross profit, gross loss and net profit after modeled costs.
- [ ] P6.7 Report profit factor and expectancy per trade.
- [ ] P6.8 Report maximum equity drawdown and drawdown duration.
- [ ] P6.9 Report average R multiple and distribution of R outcomes.
- [ ] P6.10 Report exposure, concurrent positions and daily loss statistics.
- [ ] P6.11 Break results down by London, New York and Asian sessions using an explicit timezone/session definition.
- [ ] P6.12 Break results down by month and market regime where practical.
- [ ] P6.13 Split data chronologically into development/in-sample and untouched out-of-sample periods.
- [ ] P6.14 Avoid random shuffling of time-series data.
- [ ] P6.15 Test parameter sensitivity; avoid selecting a single lucky parameter combination.
- [ ] P6.16 Run cost stress tests with wider spreads and higher slippage.
- [ ] P6.17 Run robustness tests on different date ranges and market conditions.
- [ ] P6.18 Record all parameter sets and data ranges used.
- [ ] P6.19 Produce a reproducible validation report.
- [ ] P6.20 Define go/no-go criteria before reviewing final out-of-sample results.

**Acceptance gate:** Results are reproducible, costs and assumptions are disclosed, out-of-sample results are reported separately, and no profitability claim is made from in-sample results alone.

### Phase 7 — Future demo-trading POC (not part of the backtester implementation)

Start only after Phase 6 is reviewed and explicitly approved.

- [ ] P7.1 Freeze and version the validated strategy rules.
- [ ] P7.2 Define a strategy interface shared by backtest and demo adapters.
- [ ] P7.3 Design Python-to-MQL5 message contract with schema/versioning.
- [ ] P7.4 Implement MQL5 EA receiver and health/status reporting.
- [ ] P7.5 Implement demo-only order execution adapter.
- [ ] P7.6 Add account/server/symbol allow-list checks.
- [ ] P7.7 Add explicit demo-account guard and fail-closed behavior.
- [ ] P7.8 Add order idempotency and duplicate-signal protection.
- [ ] P7.9 Validate broker volume, tick size, stops level and filling modes before sending.
- [ ] P7.10 Add execution reconciliation between requested and broker-confirmed orders.
- [ ] P7.11 Add position monitoring, SL/TP verification and emergency disable.
- [ ] P7.12 Run demo forward testing and compare live signals with backtest expectations.
- [ ] P7.13 Record spread, slippage, latency, rejection and execution-quality metrics.
- [ ] P7.14 Define a separate approval gate before any future live-trading capability. Live trading is out of scope.

### Phase 8 — Future UI application (not part of the backtester implementation)

- [ ] P8.1 Define dashboard information architecture and user roles.
- [ ] P8.2 Show live/historical M15 and M5 charts with liquidity zones.
- [ ] P8.3 Show a decision timeline: zone -> sweep -> MSS -> retest -> signal -> execution.
- [ ] P8.4 Show why each setup was accepted or rejected.
- [ ] P8.5 Show open positions, closed trades, SL/TP and P&L.
- [ ] P8.6 Show backtest configuration and report comparison.
- [ ] P8.7 Add accessible keyboard/tab navigation.
- [ ] P8.8 Add loaders/progress indicators for long-running processes.
- [ ] P8.9 Add clear error states and toast notifications.
- [ ] P8.10 Add confirmation and completion micro-interactions for final process stages.
- [ ] P8.11 Add audit logs and safe account/credential handling.
- [ ] P8.12 Add WhatsApp notifications only after notification preferences and delivery failures are handled.

---

## 7. Initial configuration defaults

These are starting research parameters, not proven optimal values. Keep all of them configurable and record them in each run.

| Parameter | Initial value | Notes |
|---|---:|---|
| Symbol | Resolve from MT5 | Do not assume exact broker symbol spelling |
| HTF | M15 | Liquidity context |
| LTF | M5 | MSS/retest/entry |
| Risk per trade | 0.5% | Simulation default only |
| Target | 2R | Configurable |
| Maximum concurrent positions | 1 | Conservative initial simulation |
| Pivot lookback | 2 left / 2 right | Must wait for right-side confirmation |
| MSS timeout | Configurable | Express in completed M5 bars |
| Retest timeout | Configurable | Express in completed M5 bars |
| Sweep tolerance | Symbol-aware | Derive from tick size/points |
| Spread | Historical if reliable; otherwise configured | Never silently assume zero |
| Commission | Broker/account-specific | Explicitly configure or disclose exclusion |
| Slippage | Configurable stress assumption | Report sensitivity |
| Session filter | Disabled initially | Add only as a separately tested filter |

Do not hardcode a fixed monetary value for XAUUSD tick size, tick value, contract size, pip size or volume step. Query the selected broker symbol and validate returned metadata.

---

## 8. Backtest correctness and anti-bias requirements

These requirements apply to every phase and are release blockers.

1. **No look-ahead bias:** pivots, zones, sweeps and MSS levels are available only after their confirmation time.
2. **No unfinished candles:** decisions use completed bars only.
3. **Correct event order:** process data chronologically and never evaluate a signal using later candles.
4. **Next executable price:** entries occur after the signal is known, using the next available executable quote/model.
5. **Bid/ask awareness:** buys and sells must use appropriate sides of the market.
6. **Conservative ambiguous-bar handling:** use tick data when available; otherwise assume the adverse outcome when both SL and TP are touched in one bar.
7. **Realistic costs:** disclose spread, commission, slippage and swap assumptions.
8. **Broker metadata:** calculate volume and monetary risk using actual symbol properties.
9. **No survivorship/selection bias:** retain failed, expired and rejected setups in diagnostic counts.
10. **Reproducibility:** same data, configuration and code version must produce the same trade ledger and summary.
11. **Out-of-sample discipline:** never tune against the final holdout period.
12. **Honest reporting:** show all trades and include losing periods; never present illustrative values as actual results.

---

## 9. Required reporting metrics

Every completed backtest should include:

- Symbol, broker server label (non-sensitive), timeframe and date range.
- Data source, data retrieval time, candle count and data-quality warnings.
- Strategy version and complete parameter configuration.
- Starting and ending balance/equity.
- Total setups, accepted signals, rejected/expired setups and executed simulated trades.
- Wins, losses and breakeven trades.
- Win rate.
- Gross profit, gross loss and net profit after modeled costs.
- Profit factor.
- Expectancy per trade.
- Average win, average loss and average R.
- Maximum equity drawdown (absolute and percentage).
- Longest losing streak and largest single loss.
- Spread, commission, slippage and swap totals or explicit exclusions.
- Session/month breakdowns.
- Out-of-sample results separately from in-sample results.
- Trade-by-trade CSV and run-summary JSON.

Do not use win rate alone to judge the strategy. A high win rate can still lose money if average losses and costs are too large.

---

## 10. Testing strategy

### Unit tests

Test each deterministic component independently:

- Candle validation and M5-to-M15 aggregation.
- Swing highs/lows and confirmation delay.
- Equal-high/equal-low clustering.
- BSL/SSL zone lifecycle.
- Sweep conditions.
- MSS conditions.
- Retest/rejection conditions.
- Setup state transitions and expiry.
- Position sizing and volume rounding.
- Long/short P&L and transaction costs.
- SL/TP collisions and gap handling.
- Metrics calculations.

### Integration tests

- MT5 adapter retrieves a small historical range.
- Symbol metadata is mapped correctly.
- Data cache can be written/read consistently.
- Full strategy pipeline processes fixture data.
- Full backtest produces a stable ledger and report.
- CLI returns expected exit codes and errors.

### Regression tests

Maintain fixed candle fixtures with manually reviewed expected pivots, zones, sweeps, signals, entries and exits. Any intentional behavior change must update the fixture and explain why.

### Definition of done for every task

A task is done only when:

- Implementation is complete.
- Relevant automated tests pass.
- Edge cases and failure modes are handled.
- Logs/errors are understandable.
- No secrets or credentials are introduced.
- Evidence/output is recorded.
- The task checkbox is updated only after verification.

---

## 11. Phase gates and project governance

| Gate | Required evidence | May proceed to |
|---|---|---|
| G0 | Existing MT5 read-only connectivity and data retrieval verified | Phase 1 |
| G1 | Validated M5/M15 data and quality report | Phase 2 |
| G2 | Correct swing and liquidity-zone fixtures | Phase 3 |
| G3 | Correct sweep/MSS/retest fixtures and no look-ahead | Phase 4 |
| G4 | Hand-calculated fills, sizing and P&L tests pass | Phase 5 |
| G5 | End-to-end CLI and reproducible exports work | Phase 6 |
| G6 | Robustness and out-of-sample report reviewed; explicit owner approval | Phase 7 planning |
| G7 | Demo-only execution safety and reconciliation validated | Future UI/live-readiness review |

The coding agent must:

- Work one atomic task at a time.
- State the task ID before starting.
- Inspect current repository code before modifying it.
- Avoid rewriting working connectivity code without a demonstrated reason.
- Run the narrowest relevant tests after each change, then broader tests at phase gates.
- Report files changed, commands run, test results and unresolved issues.
- Stop at a failed gate and explain the blocker.
- Never claim a test passed unless it was actually run and its result observed.
- Never place real trades during the terminal-backtester phases.
- Never enable live trading as an implicit follow-up.

---

## 12. Suggested terminal commands (target interface)

Exact command syntax may be refined during Phase 5, but keep the interface simple and scriptable.

```powershell
# Inspect MT5 connectivity and symbol metadata (read-only)
python -m mt5_liquidity.cli inspect

# Fetch and validate historical XAUUSD M5 data
python -m mt5_liquidity.cli fetch --symbol XAUUSD --timeframe M5 --from 2025-01-01 --to 2025-12-31

# Run the strategy backtest
python -m mt5_liquidity.cli backtest --symbol XAUUSD --from 2025-01-01 --to 2025-12-31 --risk 0.5 --rr 2

# Display help
python -m mt5_liquidity.cli --help
```

The commands above describe the intended interface; they are not executable until the corresponding phases have been implemented.

---

## 13. Final project objective

The first deliverable is a trustworthy, reproducible **terminal backtester**, not a trading bot that merely produces attractive example profits.

The backtester must answer:

1. Does the precisely defined M15 liquidity sweep + M5 MSS/retest strategy generate valid signals?
2. How often does it trade?
3. What are its net results after realistic costs?
4. What drawdown and losing streaks should be expected from the tested sample?
5. Does performance persist on unseen data and under stressed execution assumptions?
6. Is the evidence strong enough to justify a separate demo-account forward test?

Only after these questions have been investigated should the project move to the automated MT5 demo POC and then the UI application.
