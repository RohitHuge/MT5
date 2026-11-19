# Agent A4 — Completion Report

**Agent:** A4  
**Track:** Platform Services — Shadow Tracker  
**Phase:** 2B  
**Session:** 53d45df3-4bdd-4e28-a6ed-ab35159086ef (branch A2)  
**Date:** 2026-11-19  
**Git commits:** `24fc8c5` (service), `8a37cd9` (this report)  
**Status:** COMPLETE

---

## Assignment

Implement **Phase 2B** from `PLATFORM_PLAN.md` — the Shadow Tracker service.

This is the bookkeeper for every shadow-mode strategy. It receives signals that
would have been real trades, records them as virtual trades in Postgres, watches
each candle to detect SL/TP hits, and aggregates daily P&L so the dashboard can
compare all strategies on equal terms.

---

## Context From Prior Agents

Before writing any code, read A1 and A3 reports.

**From A1 (Phase 1A — MT5 Bridge):**
- `/symbol/XAUUSD` returns `tick_value` and `point` — fetch on startup for PnL calc
- `/ws/candles` does NOT buffer missed candles — rebuild from DB on restart instead

**From A3 (Phase 2A — Candle Feed + Executor):**
- Candle-feed dual-writes every candle: `RPUSH candles:XAUUSD:M5:history` (capped
  list, 500 max) + `PUBLISH candles:XAUUSD:M5` (live broadcast)
- Shadow-tracker subscribes to the **PubSub channel** `candles:XAUUSD:M5` for live
  candles — the history list is primarily for strategy containers seeding their window
- Build context must be repo root (not service dir) so `shared/` is accessible —
  A3 established this pattern; A4 follows the same convention
- Signal format: `Signal.from_json(raw)` from `shared/models.py`

---

## Tasks Completed

| Task | Description | Status |
|---|---|---|
| 2B.1 | shadow-tracker service (main.py, Dockerfile, requirements.txt) | Done |
| 2B.2 | Daily summary aggregation at UTC midnight | Done (inside main.py) |
| — | docker-compose.yml: replace stub with build directive | Done |

---

## Files Produced

```
services/shadow-tracker/
├── main.py           — full service (~270 lines)
├── requirements.txt  — redis, psycopg2-binary, requests, python-dotenv
└── Dockerfile        — build context = repo root; PYTHONPATH=/app

docker-compose.yml    — shadow-tracker stub replaced with build: directive
```

---

## How It Works

```
Strategy container (MODE=shadow)
        │ PUBLISH signals:shadow:liq-sweep  <Signal JSON>
        ▼
  shadow-tracker  ←── psubscribe("signals:shadow:*")
        │
        ├── insert_virtual_trade() → DB row id
        └── _open_trades[id] = {entry, sl, tp, lots, direction, ...}

candle-feed
        │ PUBLISH candles:XAUUSD:M5  <Candle JSON>
        ▼
  shadow-tracker  ←── subscribe("candles:XAUUSD:M5")
        │
        └── for each open trade:
              BUY:  low  <= sl? → SL hit
                    high >= tp? → TP hit
              SELL: high >= sl? → SL hit
                    low  <= tp? → TP hit
              → close_virtual_trade() + remove from _open_trades

UTC midnight (threading.Timer)
        └── aggregate yesterday's virtual_trades → upsert daily_summary
```

---

## Architecture Decisions

### 1. In-memory dict keyed by DB id (not by plan spec's tuple)

The plan spec suggested keying by `(strategy, entry_price, direction)`. That key
is not unique: two signals from the same strategy at the same price in the same
direction would collide and silently drop one trade.

**Used DB `id`** (integer, auto-increment primary key). `insert_virtual_trade()`
returns it. On restart, `get_open_virtual_trades()` returns the same `id` values
so the dict rebuilds identically — restart-safe with no data loss.

### 2. SL takes precedence when both SL and TP hit in the same candle

Same rule as the existing backtester in `backtesting/`. If a candle's range
spans both prices, we can't know order from OHLC alone. Conservative assumption:
SL hit first (worst outcome). `_check_hit()` checks SL before TP.

### 3. Build context = repo root (pattern established by A3)

A3 noted this first. All services that `import shared.*` must set:
```yaml
build:
  context: .
  dockerfile: services/<name>/Dockerfile
```
Shadow-tracker follows this same convention. The Dockerfile:
```dockerfile
COPY services/shadow-tracker/requirements.txt .
COPY shared/ ./shared/
COPY services/shadow-tracker/main.py .
ENV PYTHONPATH=/app
```

### 4. Fresh psycopg2 connection for the midnight aggregation

The daily summary runs in a `threading.Timer` callback — a one-shot query that
fires once every 24 hours. Opening a fresh `psycopg2.connect()` here (rather than
borrowing from `shared.db`'s `ThreadedConnectionPool`) avoids holding a pool
connection idle all day and avoids pool exhaustion during peak hours.

### 5. Midnight timer fires at 00:00:05 UTC

5-second offset guarantees all strategy containers have finished publishing
signals for the day before the aggregation reads the DB. Edge case avoided:
strategy emits signal at 23:59:59, aggregation fires at 00:00:00 before the
signal is persisted.

### 6. Symbol metadata fetched with retry (10 × 5s)

The MT5 bridge may not be ready the instant the container starts. Up to 50 seconds
of retry before falling back to safe defaults (`tick_value=1.0, point=0.01`). If
defaults are used a WARNING is logged — operator will see it and can restart.

### 7. Heartbeat includes open trade count

```json
{"service": "shadow-tracker", "ts": "2026-11-19T...", "open_trades": 3}
```

Published to `heartbeat:shadow-tracker` every 10 seconds. The count lets the
dashboard (Phase 3A) show a live "X positions being tracked" indicator and makes
it obvious if the service has stalled.

---

## Integration Points

**Consumes:**

| Source | Channel / Endpoint |
|---|---|
| Strategy containers (`MODE=shadow`) | `signals:shadow:*` (Redis psubscribe) |
| candle-feed | `candles:XAUUSD:M5` (Redis subscribe) |
| mt5-bridge | `GET /symbol/XAUUSD` — once on startup |

**Produces:**

| Consumer | What |
|---|---|
| dashboard (Phase 3A) | `virtual_trades` rows in Postgres |
| dashboard (Phase 3A) | `daily_summary` rows (`is_shadow=TRUE`) in Postgres |
| dashboard (Phase 3A) | `heartbeat:shadow-tracker` on Redis |

---

## What Phase 3A (Dashboard) Builder Needs to Know

1. **`daily_summary` rows write at UTC midnight for the previous day.** Day 1 has
   no rows — handle empty results gracefully everywhere (0 trades, $0 PnL, blank chart).

2. **`virtual_trades` with `outcome='OPEN'` are live positions** — filter
   `outcome != 'OPEN'` for completed trade history. Show OPEN rows separately
   as "shadow positions currently tracking".

3. **`is_shadow=TRUE` flags virtual trades** — `daily_summary` has rows for both
   real (executor, `is_shadow=FALSE`) and virtual (shadow-tracker, `is_shadow=TRUE`).
   The comparison view should show both for the live strategy so the user sees
   "real P&L vs what shadow model would have made on the same days."

4. **`db.get_comparison_summary(days)`** already JOINs `strategies` for
   `display_name` and `mode` — no second query needed for the comparison table.

5. **`db.swap_live_strategy(new_live)`** is the correct call when user promotes
   a strategy in the UI. It's a single atomic transaction. Never call
   `set_strategy_mode` twice — the unique index will reject it mid-swap.

6. **`CANDLE_HISTORY_KEY`** (`candles:XAUUSD:M5:history`) — A3 noted this Redis
   List constant needs adding to `shared/channels.py`. Shadow-tracker doesn't
   use it (rebuilds from DB), but strategy containers (Phase 2C) and dashboard
   do. Add it before Phase 2C/3A work begins.

---

## Problems Encountered

### Problem 1 — Plan's in-memory key is not unique

Spec said key by `(strategy, entry_price, direction)`. Collides on duplicate
signals. Fixed: key by DB `id` (see Decision 1).

### Problem 2 — Build context excludes shared/

`build: ./services/shadow-tracker` leaves `shared/` outside the Docker context.
Fixed: `build: { context: ., dockerfile: ... }` (see Decision 3, same as A3).

### Problem 3 — Plan mode activated mid-implementation

Plan mode was triggered after the directory was created, blocking all file writes.
Exited immediately via `ExitPlanMode` and resumed.

---

## Pending Cleanup Items

The following are non-blocking but should be done before production:

| Item | File | Notes |
|---|---|---|
| Add `CANDLE_HISTORY_KEY` constant | `shared/channels.py` | Needed by Phase 2C builders (A3 noted this) |
| Add `get_open_real_trades()` | `shared/db.py` | Executor queries this inline; cleaner as a shared function (A3 noted this) |
| Remove `version: "3.9"` | `docker-compose.yml` | Triggers deprecation warning in Compose v5 (A2 noted this) |

---

## Blockers / Notes for Lead

None. Phase 2B is fully coded and committed.

Phase 2C (strategy containers) can proceed in parallel with Phase 3A (dashboard).
Both depend on Phase 2A (candle-feed) being running, not on Phase 2B specifically.
Shadow-tracker will start producing data as soon as strategy containers emit their
first shadow signals.
