# Phase 2 Implementation Report — Time-Based Performance Analytics

**Branch:** `main`
**Scope:** `/backtesting` only
**Date:** 2026-11-11
**Phase status:** Implemented and verified locally. All acceptance criteria in `IMPLEMENTATION_PLAN.md` section 2I were executed and passed.

## 1. What was implemented

Phase 2 adds a time-based performance analytics layer on top of the existing
backtest engine, without modifying any strategy signal rule or execution rule:

- **Trade duration analytics** — holding time from `fill_ts` to `close_ts`,
  bucketed into configurable ranges (default 0–5 / 5–15 / 15–30 / 30–60 /
  60–120 / 120+ minutes), with average, median, min, max, same-candle exit
  count, and winner/loser duration cohorts.
- **Hourly performance** — all 24 UTC entry hours, including zero-trade
  hours, with count, wins/losses, win rate, gross profit/loss, net P&L,
  average P&L, profit factor, and a documented drawdown-contribution metric.
- **Trading-session performance** — DST-aware Tokyo / London / New York
  windows plus a derived London–NY overlap and an off-session bucket,
  classified with a documented mutually exclusive precedence.
- **Daily / weekly / monthly performance** — exit-date (realised cash-flow)
  attribution with separate entry-cohort columns, ISO-week grouping with ISO
  year, zero-trade periods included, and per-period mark-to-market equity
  open/close/high/low/return/drawdown.
- **Mark-to-market equity and drawdown** — the engine now records an
  end-of-candle account observation (realised equity, floating P&L,
  mark-to-market equity, open-position count). Floating P&L uses the
  existing bid/ask valuation (`OpenPosition.unrealized_pnl`), so SELL marks
  include the spread. Peak, trough, maximum drawdown (USD and %), recovery
  timestamp, and time-underwater are computed from this series.
- **Accounting reconciliation** — every aggregation is checked against the
  trade ledger (counts, net P&L, gross profit/loss, final equity) and the
  results are exported in `analytics_summary.json`.
- **Report integration** — schema-version-2 report bundles add an
  `analytics/` subdirectory; existing files keep their names and meaning.
- **Console summary** — `TIME ANALYTICS (Phase 2)` section printed by the CLI.

## 2. Files created and modified

**Created**

| File | Purpose |
|---|---|
| `src/mt5_liquidity/reporting/analytics/__init__.py` | `build_analytics_bundle()` orchestrator, reconciliation, headline metrics |
| `src/mt5_liquidity/reporting/analytics/models.py` | Typed analytics data models (TradeGroupStats, DurationBucket, HourRow, SessionWindow, PeriodRow, DrawdownSummary, Reconciliation, AnalyticsBundle) |
| `src/mt5_liquidity/reporting/analytics/aggregation.py` | Shared helpers: group aggregation, max-drawdown from a series, period streaks, duration, median |
| `src/mt5_liquidity/reporting/analytics/duration.py` | Duration buckets and duration statistics |
| `src/mt5_liquidity/reporting/analytics/hourly.py` | Entry-hour grouping (UTC canonical + display column) |
| `src/mt5_liquidity/reporting/analytics/sessions.py` | DST-aware session windows, classification, session aggregation |
| `src/mt5_liquidity/reporting/analytics/periods.py` | Daily/weekly/monthly rows, period enumeration, period equity |
| `src/mt5_liquidity/reporting/analytics/equity.py` | Mark-to-market drawdown analytics |
| `tests/unit/test_analytics_aggregation.py` | 13 tests |
| `tests/unit/test_analytics_duration.py` | 10 tests |
| `tests/unit/test_analytics_hourly.py` | 9 tests |
| `tests/unit/test_analytics_sessions.py` | 14 tests |
| `tests/unit/test_analytics_periods.py` | 14 tests |
| `tests/unit/test_analytics_equity.py` | 10 tests |
| `tests/unit/test_reporting_analytics_export.py` | 10 tests |
| `tests/unit/test_console_encoding.py` | 1 regression test |
| `PHASE2_IMPLEMENTATION_REPORT.md` | This report |

**Modified**

| File | Change |
|---|---|
| `src/mt5_liquidity/backtest/engine.py` | Added `MarkToMarketPoint` and per-candle mark-to-market capture (`BacktestResult.mark_to_market_curve`, default empty for backward compatibility). Final point after force-closes reconciles with `final_equity`. No execution rules changed. |
| `src/mt5_liquidity/config.py` | Added `analytics_display_timezone`, `duration_bucket_minutes`, `session_overrides` (all with backward-compatible defaults). |
| `src/mt5_liquidity/reporting/exporter.py` | Schema version 2: writes `analytics/` files, metadata `analytics` section, extended completeness validation, optional pre-built bundle parameter. |
| `src/mt5_liquidity/reporting/metrics.py` | `print_analytics_summary()` console section. |
| `src/mt5_liquidity/cli.py` | Builds the bundle once and passes it to the exporter and console summary. |
| `src/mt5_liquidity/reporting/console.py` | `ensure_printable_stdout()` — see issue #2 below. |
| `tests/unit/test_reporting_exporter.py` | Top-level file-set assertion now includes the `analytics` directory (intentional schema-v2 change). |
| `IMPLEMENTATION_PLAN.md` | Phase 2 checklist updated after verification; status header updated. |

## 3. Analytics formulas and definitions

- **TradeGroupStats** (used everywhere): winners = net P&L > 0; losers =
  net P&L ≤ 0 (matches `TradeLedger`); `breakeven` = net == 0 subset of
  losers, so winners + losers == trade_count always. gross_profit = Σ gross
  P&L > 0; gross_loss = Σ gross P&L ≤ 0; net = gross_profit + gross_loss −
  commission; profit_factor = gross_profit / |gross_loss| (∞ when no losses,
  exported as `"inf"`); win_rate = winners / count.
- **Duration** = (close_ts − fill_ts) in minutes. Buckets are lower-inclusive
  / upper-exclusive (exactly 15 min → 15–30m); the final bucket is open-ended.
- **Hourly drawdown contribution** = Σ net P&L of losing trades in the hour
  (≤ 0): how much equity that entry hour gave back.
- **Period equity** from the mark-to-market series: open carries the last
  observation before the period forward; close is the last observation in (or
  carried to) the period; high/low over in-period observations; return =
  (close − open) / open; period max drawdown evaluated on a series seeded
  with the carried-open point.
- **Mark-to-market drawdown**: peak = running maximum equity; drawdown =
  peak − equity; trough = lowest equity of the max-drawdown event;
  recovery = first time equity returns to the event peak; underwater =
  peak_ts → recovery_ts (or series end if never recovered).
- **MAE/MFE**: not fabricated — labelled `unavailable (no intratrade
  bar-path observations in Phase 2)` in every duration row, per plan 2C.

## 4. Timezone policy

- All computation uses UTC-aware timestamps; candle and trade timestamps
  are already UTC in this codebase.
- Canonical grouping boundaries (hours, days, ISO weeks, months) are UTC.
- Exported timestamps are ISO-8601 UTC.
- `config.analytics_display_timezone` (default `UTC`) adds a display-only
  local-hour column to hourly analytics; it never changes grouping.
- Sessions use their own regional IANA timezones (see below), evaluated
  per-instant via `zoneinfo`, so DST transitions are handled correctly.
- No machine-local timezone is ever used for analytics.

## 5. Session definitions and attribution policy

Defaults (configurable via `BacktestConfig.session_overrides`, validated at
resolution time — unknown names, malformed `HH:MM`, or unknown IANA zones
raise `ValueError`):

| Session | Local window | Timezone |
|---|---|---|
| TOKYO | 09:00–15:00 | Asia/Tokyo |
| LONDON | 08:00–17:00 | Europe/London |
| NEW_YORK | 08:00–17:00 | America/New_York |
| LONDON_NY_OVERLAP | intersection of London & NY (per instant) | — |
| OFF_SESSION | everything else | — |

Windows are `[start, end)` in local wall time; DST is handled by `zoneinfo`
per-instant (verified across the 2026 US and EU DST transitions in tests).

**Attribution:** each trade is attributed to exactly one primary session by
its fill timestamp using the precedence LONDON_NY_OVERLAP > LONDON >
NEW_YORK > TOKYO > OFF_SESSION. Classification is mutually exclusive, so
session trade counts always sum to the closed-trade population; the overlap
row is a subset of the London and New York windows by definition.

## 6. Mark-to-market valuation methodology

- At the end of every M5 candle (after exits and entries are processed) the
  engine records `MarkToMarketPoint(timestamp, realised_equity,
  floating_pnl, mtm_equity, open_positions)`.
- Floating P&L reuses `OpenPosition.unrealized_pnl`: BUY marks at the bid
  close; SELL marks at the ask close (bid close + spread), so the spread
  cost of closing a short is visible. Commission is already deducted from
  realised equity at close time; swap remains excluded and is disclosed as
  such (`cost_note`).
- No future candles are used: each observation only sees positions known at
  that candle and that candle's close (regression-tested).
- After end-of-series force-closes the engine appends a final zero-floating
  observation so `mtm_equity` reconciles with `stats.final_equity`.
- `equity_curve.csv` is unchanged and now explicitly documented as the
  closed-trade (realised) equity series; mark-to-market data lives in
  `analytics/mark_to_market_equity.csv`.
- **Limitation:** M5 OHLC provides one mark per candle (the close).
  Intrabar equity paths are not reconstructable from OHLC data, so drawdown
  between candle closes is unobservable; end-of-candle marks are the
  documented conservative valuation. This note is exported in
  `analytics_summary.json`.

## 7. Known limitations

1. Intrabar MTM path not observable (see above) — drawdown is
   end-of-candle-conservative.
2. MAE/MFE unavailable until intratrade bar-path observations are captured
   (labelled, not fabricated).
3. Exit-hour analytics intentionally not produced in Phase 2 (entry-hour
   and exit-hour attribution must not be mixed; a separate exit report can
   be added later).
4. Hourly "drawdown contribution" is a loss-attribution metric, not a
   per-hour equity drawdown (which would require an entry-hour-conditioned
   equity path).
5. Sessions classify by time-of-day in regional zones regardless of
   weekday; XAUUSD weekend candles do not exist in this dataset, so this
   has no practical effect, but the assumption is documented.
6. `duration_analysis.csv` reports MAE/MFE as a per-row literal label until
   the data contract for intratrade paths exists.

## 8. Test cases added

93 new tests (310 total, all passing):

- **Aggregation (13):** empty groups, win/loss/breakeven classification,
  commission handling, gross semantics, infinite profit factor, averages,
  order-independence; drawdown peak/trough/recovery/underwater, unrecovered
  drawdown, monotonic series, higher-peak reset; period streaks; duration
  validation (missing/inverted timestamps); median.
- **Duration (10):** bucket placement, exact-boundary inclusivity,
  zero-duration/same-candle counts, duration statistics, winner/loser
  cohorts, bucket-to-population reconciliation, empty input, custom edges,
  invalid edges rejected.
- **Hourly (9):** 24 rows always, grouping, empty hours, midnight boundary,
  count/net reconciliation, drawdown contribution, display-timezone
  conversion (UTC→Asia/Kolkata), UTC identity, unknown timezone rejected.
- **Sessions (14):** default resolution, overrides, invalid override
  rejection (name/format/timezone), window boundary exclusivity, Tokyo GMT
  offset, overlap precedence in winter, US-DST-spring gap week, EU-DST-ends
  first week, NY extension after London close, mutually exclusive counting,
  no double counting, empty input.
- **Periods (14):** exit-date attribution for cross-midnight trades,
  zero-trade days incl. weekends with carry-forward equity, daily equity/
  drawdown, count reconciliation, ISO-week grouping, weekend spanning two
  ISO weeks, ISO year boundary (2025-12-29 → 2026-W01), month boundary,
  cross-month attribution, period streaks, empty input, MTM-only ranges,
  unknown granularity, cross-granularity net-P&L reconciliation.
- **Mark-to-market (10):** no-trade curve, no-candle curve, floating loss →
  recovery → TP, no-look-ahead per-point valuation, SELL ask-side spread
  visibility, multiple open positions, force-close reconciliation, direct
  point analytics, empty summary.
- **Exporter analytics (10):** complete file set, 24-hour CSV, session
  reconciliation, duration buckets, period CSV reconciliation, MTM CSV,
  summary JSON schema + reconciliation flags, metadata analytics section,
  empty-dataset safety, no secrets.
- **Console encoding (1):** cp1252-stdout regression (issue #2 below).

## 9. Full test-suite results

```
tests/unit         306 passed
tests/integration    4 passed
---------------------------
total              310 passed, 0 failed, 0 skipped  (pytest 9.1.1, Python 3.13.5)
```

Sample backtest (live MT5 demo terminal, cached/fetched XAUUSD M5
2026-08-13 → 2026-11-10, 17,353 candles): completed end-to-end; 540 setups,
31 ENTRY_READY, 20 trades; net P&L −$472.39; final equity $9,527.61.
Report `reports/XAUUSD/2026-11-11_06-43-09_31909464` published to GitHub
(commit `56349a7`) including the full `analytics/` bundle.

**Accounting reconciliation on the historical sample** (from
`analytics_summary.json`): counts 20 == hourly 20 == session 20 == duration
20 == daily exit 20 == daily entry 20; daily/weekly/monthly net P&L sums
each −472.39 == ledger; gross profit 268.40 and gross loss −740.79 match;
final MTM equity 9,527.61 == ledger final equity — all flags `true`.

## 10. Issues discovered but not fixed (or minimally fixed)

1. **Pre-existing:** MT5 timestamps in the fetched cache do not cover the
   declared metadata range end (warning from `CandleCache.load`, pre-dates
   Phase 2; the loader correctly falls back to a fresh fetch). Left
   unchanged — data-layer behaviour, not Phase 2 scope.
2. **Fixed (minimum correction):** `UnicodeEncodeError` on cp1252 Windows
   consoles — `print_header`'s Unicode arrow (`→`) aborted the CLI after
   the backtest had run but **before the report was saved**. Fix:
   `reporting/console.ensure_printable_stdout()` reconfigures stdout with
   `errors="replace"` at CLI start (unencodable characters are substituted,
   nothing else changes). Regression test added. Found because the Phase 2
   sample run crashed on a non-UTF-8 console.
3. **Pre-existing, documented:** 5 incomplete M15 windows are dropped from
   the backtest with a warning (existing behaviour, surfaced in metadata).
4. **Observation for the later audit (not fixed):** the 2026-08-13 →
   11-10 sample shows all 5 same-candle exits were stop-losses and all 8
   Tokyo-session trades lost. Recorded as analytics findings only — no
   strategy change was made.

## 11. Phase 3 confirmation

No Phase 3 functionality was implemented: there is no daily profit-target
protection, no additional daily-loss enforcement, no equity trailing
protection, no account-level max-drawdown shutdown, no loss-streak
shutdown, and no persistent risk state. No strategy entry/exit rule was
changed and no strategy parameter was tuned. The engine change is
strictly observational (equity recording), and the analytics package is
read-only over engine output.

Nothing in this report or the new analytics constitutes evidence of
profitability; the sample result is a simulated historical outcome with
disclosed limitations.
