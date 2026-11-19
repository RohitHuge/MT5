# Agent A4 — Completion Report

**Agent:** A4  
**Track:** Platform Services — Shadow Tracker  
**Session:** 53d45df3-4bdd-4e28-a6ed-ab35159086ef (branch A2)  
**Date:** 2026-11-19  
**Phase:** 2B  
**Status:** COMPLETE

---

## Assignment

Implement **Phase 2B** from `PLATFORM_PLAN.md` — the Shadow Tracker service.

This service watches every shadow-mode strategy signal, records each one as a
virtual trade in Postgres, and closes it when subsequent candles trigger the
SL or TP. It is the data source that makes the dashboard comparison meaningful:
all historical virtual P&L lives here.

---

## Tasks Completed

| Task | Description | Status |
|---|---|---|
| 2B.1 | Create shadow-tracker service (main.py, Dockerfile, requirements.txt) | Done |
| 2B.2 | Daily summary aggregation at UTC midnight | Done (inside main.py) |
| — | Update docker-compose.yml with build directive | Done |

---

## Files Produced

```
services/shadow-tracker/
├── main.py           — full service (~270 lines)
├── requirements.txt  — redis, psycopg2-binary, requests, python-dotenv
└── Dockerfile        — build context = repo root; PYTHONPATH=/app

docker-compose.yml    — shadow-tracker stub replaced with build: directive
```

**Git commit:** `24fc8c5`  
`feat(platform): Phase 2B -- shadow-tracker service`

---

## Architecture Decisions

### 1. In-memory dict keyed by DB id, not by (strategy, entry_price, direction)

The plan spec suggested keying by `(strategy, entry_price, direction)`. That key
is not unique — two different signals from the same strategy at the same entry
price in the same direction (e.g. two signals fired on consecutive candles) would
collide and lose one trade.

**Solution:** Key by the integer DB `id` returned from `insert_virtual_trade()`.
The DB auto-increments the id so it is always unique. On restart, `get_open_virtual_trades()`
returns the same id values, so the dict is rebuilt identically.

### 2. SL takes precedence when both SL and TP are hit in the same candle

If a candle's range is wide enough to touch both the stop-loss and take-profit
prices, we cannot know from bar data alone which was hit first. The conservative
convention (matching the existing backtester in `backtesting/`) is to assume
the worst: SL hit, trade closed at a loss. `_check_hit()` checks SL before TP
for this reason.

### 3. Build context is the repo root, not services/shadow-tracker/

`shared/` lives at the repo root and must be `COPY`-ed into every service image.
If the Dockerfile's build context were `services/shadow-tracker/`, Docker would
not see `shared/` and the build would fail.

**Solution in docker-compose.yml:**
```yaml
build:
  context: .
  dockerfile: services/shadow-tracker/Dockerfile
```

The Dockerfile then copies:
```dockerfile
COPY services/shadow-tracker/requirements.txt .
COPY shared/ ./shared/
COPY services/shadow-tracker/main.py .
```

**This same pattern must be used for every service that imports from `shared/`.**
(candle-feed, executor, strategy containers, dashboard — all Phase 2+.)

### 4. Separate psycopg2 connection for the midnight aggregation query

The daily aggregation runs in a `threading.Timer` thread at UTC midnight. Rather
than reusing `shared/db`'s private connection pool (which is designed for the
main thread's transactional operations), it opens a fresh `psycopg2.connect()` for
the one-off GROUP BY aggregation query. This avoids holding a pool connection idle
for 24 hours and avoids any cross-thread pool state issues.

### 5. Symbol metadata fetched with retry on startup

`tick_value` and `point` are fetched from `mt5-bridge /symbol/XAUUSD` once on
startup. The bridge may not be ready immediately (it starts after the container
network comes up). The fetch retries 10 times with 5-second gaps — 50 seconds
total — before falling back to safe defaults (`tick_value=1.0, point=0.01`).
If the defaults are used, a warning is logged so the operator knows PnL figures
may be wrong.

### 6. Midnight timer fires at 00:00:05 UTC (not 00:00:00)

A 5-second offset after midnight ensures all strategies have finished publishing
their last signals for the day before the aggregation runs. This prevents an edge
case where a strategy emits a signal at 23:59:59 and the aggregation fires before
the signal is processed.

### 7. Heartbeat includes open trade count

```json
{"service": "shadow-tracker", "ts": "...", "open_trades": 3}
```

The dashboard (Phase 3A) can display this count to show the tracker is alive and
processing. It also makes it obvious if the service has stalled (count stays at 0
for a full session when strategies are running).

---

## Integration Points

| Upstream producer | What this service consumes |
|---|---|
| Any strategy container (`MODE=shadow`) | `signals:shadow:{strategy}` — one message per signal |
| `candle-feed` | `candles:XAUUSD:M5` — one message per closed candle |
| `mt5-bridge` | `GET /symbol/XAUUSD` — once at startup for tick_value and point |

| Downstream consumer | What this service produces |
|---|---|
| `dashboard` (Phase 3A) | `virtual_trades` rows (via Postgres) |
| `dashboard` (Phase 3A) | `daily_summary` rows with `is_shadow=TRUE` (via Postgres) |

---

## What Phase 3A (Dashboard) Builder Needs to Know

1. **`daily_summary` rows are written at UTC midnight for the previous day.**
   On Day 1 there will be no rows yet — the dashboard must handle empty results
   gracefully (show 0 trades, $0 P&L, no chart). Don't assume rows always exist.

2. **`virtual_trades` rows with `outcome='OPEN'` are still in-flight.**
   If the dashboard queries trade history it should filter `outcome != 'OPEN'`
   to show only completed trades, or show OPEN trades separately as "live positions".

3. **`is_shadow=TRUE` distinguishes virtual trades from real ones.**
   `daily_summary` has rows for both real (from executor) and virtual (from shadow-
   tracker) trades, differentiated by `is_shadow`. The comparison view should
   show both for the live strategy (real P&L vs what shadow would have recorded).

4. **`db.get_comparison_summary(days)` already does the JOIN** — it returns
   `strategy`, `display_name`, `mode`, `total_trades`, `total_net_pnl`,
   `win_rate_pct`, `profitable_days`. No second query needed for the comparison table.

5. **`db.swap_live_strategy(new_live)` is atomic** — when the user promotes a
   shadow strategy to live in the UI, call only this function (not two separate
   `set_strategy_mode` calls). It runs in a single transaction.

6. **The heartbeat channel is `heartbeat:shadow-tracker`** — the dashboard can
   subscribe to it to show a "last seen" timestamp and open trade count in a
   status bar.

---

## Problems Encountered

### Problem 1 — Plan's suggested in-memory key is not unique

The plan spec said to key `_open_trades` by `(strategy, entry_price, direction)`.
As described in Decision 1 above, this collides. Used DB `id` instead.

### Problem 2 — Build context for shared/ access

Standard `build: ./services/shadow-tracker` would exclude `shared/` from the
Docker build context. Switched to `build: { context: ., dockerfile: ... }`.
This affects every service that imports from `shared/` — documented in Decision 3.

---

## Blockers / Notes for Lead

None. Phase 2B is fully coded and committed.

The `services/candle-feed/` and `services/executor/` directories are visible as
untracked in `git status` — those are Phase 2A deliverables and not part of this
agent's scope.

The only runtime prerequisite before shadow-tracker can produce data is that at
least one strategy container is running in `MODE=shadow` and publishing to
`signals:shadow:*`. The service starts cleanly with zero open trades and waits.
