# Agent A2 — Completion Report

**Agent:** A2  
**Track:** Infrastructure — Database, Redis, Docker Compose  
**Session:** 53d45df3-4bdd-4e28-a6ed-ab35159086ef  
**Date:** 2026-11-19  
**Status:** COMPLETE (code), manual verification pending

---

## Assignment

Implement **Phase 1B** from `PLATFORM_PLAN.md` — Infrastructure: Database schema,
Redis + Postgres wiring in docker-compose, and the shared Python DB client used
by every service that writes or reads trade data.

---

## Tasks Completed

| Task | Description | Status |
|---|---|---|
| 1B.1 | Finalise docker-compose.yml with real redis/postgres images + health checks | Done (Phase 0 had it complete) |
| 1B.2 | DB init script mounted into postgres container | Done (Phase 0 — `shared/schema.sql`) |
| 1B.3 | Shared Python DB client (`shared/db.py`) | Done |
| 1B.4 | Verify Redis and Postgres start correctly | Manual — see verification commands below |

---

## Files Produced

```
shared/
├── __init__.py          — package marker (Phase 0)
├── models.py            — Candle, Signal, OrderResult dataclasses (Phase 0)
├── channels.py          — Redis channel name constants (Phase 0)
├── schema.sql           — Full PostgreSQL schema + indexes + seed data (Phase 0)
└── db.py                — psycopg2 DB client, all CRUD + dashboard queries (Phase 1B)

docker-compose.yml       — All 10 services, infra live, rest behind --profile platform (Phase 0)
.env.example             — Every env var documented with defaults (Phase 0)
```

**Note:** Tasks 1B.1 and 1B.2 were satisfied by Phase 0 work in the parent
session. This agent's main deliverable is `shared/db.py`.

**Git commits:**
- `64fe48e` — `feat(platform): Phase 0 — shared contracts, schema, docker-compose skeleton`
- DB client added in this session (not yet committed — see below)

---

## What `shared/db.py` Covers

### Connection
- `ThreadedConnectionPool` (min=1, max=10) — thread-safe, shared across a process
- Reads `POSTGRES_URL` from env on first use — lazy init, no import-time side effects
- Context manager `_conn()` — auto-commit on success, auto-rollback on error

### strategies table
- `get_all_strategies()` — full table, ordered by name
- `set_strategy_mode(name, mode)` — single strategy mode change
- `swap_live_strategy(new_live)` — atomically demotes current live → shadow, promotes new one; single transaction so the partial unique index (`WHERE mode = 'live'`) is never violated mid-swap

### virtual_trades table
- `insert_virtual_trade(...)` — opens a new shadow trade, returns DB id
- `close_virtual_trade(trade_id, close_price, outcome, gross_pnl, commission, net_pnl)` — marks as TP/SL/FORCE_CLOSE
- `get_open_virtual_trades(strategy=None)` — returns all OPEN rows for shadow-tracker's in-memory rebuild on restart

### real_trades table
- `upsert_real_trade(trade: dict)` — INSERT ... ON CONFLICT UPDATE by ticket; covers both new trades and position sync updates

### daily_summary table
- `upsert_daily_summary(...)` — INSERT ... ON CONFLICT UPDATE; shadow-tracker calls this at UTC midnight

### Dashboard queries
- `get_strategy_performance(strategy, days)` — daily rows for the last N days
- `get_comparison_summary(days)` — all strategies side by side with win_rate, total_net_pnl, profitable_days (JOIN with strategies for display_name + mode)
- `get_equity_curve(strategy, days, is_shadow)` — cumulative net_pnl series using window function `SUM(...) OVER (ORDER BY date)`

---

## Architecture Decisions

### 1. psycopg2 not SQLAlchemy

The plan specified psycopg2 (sync, no ORM). Chosen deliberately:
- Every service is a simple loop — no async DB needed
- SQL is explicit and readable; no ORM magic to debug at 2 AM
- `psycopg2-binary` is a single pip install with no C compiler needed in Docker

### 2. `ThreadedConnectionPool` not per-call connections

Each `connect()`/`close()` pair adds ~5ms latency and risks exhausting the
Postgres `max_connections` limit (default 100). The pool keeps 1–10 connections
open and reuses them. `putconn()` is called in `finally` so connections always
return to the pool even on error.

### 3. `swap_live_strategy` in one transaction

The schema has a partial unique index `ON strategies(mode) WHERE mode = 'live'`
which enforces the one-live-at-a-time rule at the DB level. If the swap were two
separate UPDATE calls, there's a window where both are `live` simultaneously —
Postgres would raise `IntegrityError`. Running both UPDATEs in one transaction
(demote first, then promote) avoids this. The `_conn()` context manager commits
the whole transaction atomically.

### 4. Schema already complete from Phase 0

`shared/schema.sql` was written in Phase 0 with all four tables, all indexes, and
seed data for the three strategies. The docker-compose.yml mounts it at
`/docker-entrypoint-initdb.d/init.sql` — Postgres runs it automatically on first
container start. No separate migration tool needed at this stage.

### 5. `IF NOT EXISTS` throughout schema

All `CREATE TABLE` and `CREATE INDEX` use `IF NOT EXISTS`. This means the init
script is idempotent — safe to re-run if the volume is recreated or during
development resets without throwing errors.

---

## Integration Points

| Downstream service | How it uses db.py |
|---|---|
| `shadow-tracker` (Phase 2B) | `insert_virtual_trade`, `close_virtual_trade`, `upsert_daily_summary`, `get_open_virtual_trades` |
| `executor` (Phase 2A) | `upsert_real_trade`, `upsert_daily_summary` |
| `dashboard` (Phase 3A) | `get_all_strategies`, `get_comparison_summary`, `get_strategy_performance`, `get_equity_curve`, `swap_live_strategy` |

All services import from `shared.db` — no service has its own DB layer.

---

## Problems Encountered

### Problem 1 — Docker Desktop not running during 1B.4 verification

**What happened:** Task 1B.4 required verifying Redis and Postgres start correctly.
Attempted `docker-compose up -d redis postgres` — Docker Desktop service was
stopped (`com.docker.service` = Stopped). Started it via PowerShell, waited for
the daemon to become ready, but the engine returned 500 errors during initialisation.

**Root cause:** Docker Desktop needs 30–60 seconds to fully start the WSL2 Linux
engine after the Windows service starts. The polling loop was rejected by the user
before it completed.

**Resolution:** User instructed to skip runtime verification steps entirely and
just write the code. Task 1B.4 is a manual verification step.

**What to run manually:**
```powershell
cd E:\PROJECTS\MT5

# Start infra
docker-compose up -d redis postgres

# Confirm health (both should show healthy)
docker-compose ps

# Confirm schema loaded
docker exec mt5-postgres psql -U trader -d mt5 -c "\dt"
docker exec mt5-postgres psql -U trader -d mt5 -c "SELECT name, mode FROM strategies;"

# Confirm Redis
docker exec mt5-redis redis-cli ping
```

Expected output from `\dt`:
```
 public | daily_summary  | table | trader
 public | real_trades    | table | trader
 public | strategies     | table | trader
 public | virtual_trades | table | trader
```

Expected output from strategies query:
```
   name    |  mode
-----------+--------
 ema-scalp | live
 liq-sweep | shadow
 ob-fvg    | shadow
```

### Problem 2 — `version` key in docker-compose.yml is obsolete

**What happened:** Docker Compose v5 emits a warning:
```
the attribute `version` is obsolete, it will be ignored
```

**Resolution:** Not a blocker — warning only, compose file works correctly.
Can remove `version: "3.9"` from `docker-compose.yml` in a cleanup pass.

---

## Pending — Commit

The `shared/db.py` file needs to be committed. Run:

```powershell
cd E:\PROJECTS\MT5
git add shared/db.py
git commit -m "feat(platform): Phase 1B — shared Postgres DB client"
```

---

## What Phase 2 Builders Need to Know

### For Phase 2A (candle-feed + executor):

1. `upsert_real_trade(trade: dict)` expects these keys at minimum:
   `ticket, strategy, symbol, direction, lots, open_price, opened_at, is_open`
   All other fields (`close_price`, `net_pnl`, etc.) are optional.

2. The executor should call `upsert_real_trade` twice per trade:
   - On open: `is_open=True`, no pnl fields
   - On close: `is_open=False`, with `close_price`, `net_pnl`, etc.

### For Phase 2B (shadow-tracker):

1. On startup, call `get_open_virtual_trades()` to rebuild the in-memory open
   trade dict — this handles restarts without losing track of open positions.

2. `close_virtual_trade` takes keyword-only args — do not pass positionally.

3. The `gross_pnl` formula: `(close_price - entry_price) * direction_sign * lots * tick_value / point`
   where `direction_sign = 1` for BUY, `-1` for SELL.

### For Phase 3A (dashboard):

1. `swap_live_strategy(new_live)` is the correct function to call when the user
   promotes a strategy in the UI — it handles the atomic demotion + promotion in
   one transaction. Do NOT call `set_strategy_mode` twice.

2. `get_comparison_summary(days)` already JOINs `strategies` for `display_name`
   and `mode` — no second query needed to show the mode badge.

---

## Blockers / Notes for Lead

None blocking Phase 2. All code is written.

One cleanup item: remove `version: "3.9"` from `docker-compose.yml` to silence
the deprecation warning (cosmetic only, does not affect functionality).
