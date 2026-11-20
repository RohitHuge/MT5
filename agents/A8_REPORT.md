# A8 Agent Report — Replace per-container MODE with DB-controlled signals:all bus

**Date:** 2026-11-20
**Branch:** main
**Commit message:** `fix(platform): replace per-container MODE env with DB-controlled signals:all bus`

---

## Problem Fixed

Each strategy container previously read a `MODE` env var (`live` / `shadow` / `off`) and published to either `signals:live` or `signals:shadow:{strategy}`. The env var and the `strategies.mode` column in Postgres were two independent sources of truth. If two containers both had `MODE=live` (misconfiguration, copy-paste error, stale env), both published to `signals:live` and the executor placed duplicate real orders — a critical double-trade bug.

---

## Files Changed

### 1. `shared/channels.py`
- Added `SIGNAL_ALL = "signals:all"` — the new unified channel all strategies publish to.
- Updated the module docstring and inline comments.
- Kept `SIGNAL_LIVE`, `SIGNAL_SHADOW`, and `SIGNAL_SHADOW_PATTERN` with legacy comments explaining they are no longer written by strategy containers but kept for backwards compatibility (reports, docs, ad-hoc tooling may reference them).

### 2. `shared/strategy_base.py`
- Removed `self.mode = os.getenv("MODE", "shadow")` from `__init__`.
- Removed the `if self.mode == "off": continue` guard in the main candle loop.
- Removed the `if/elif MODE == live/shadow` branching in `_publish_signal()`. The method now always publishes to `SIGNAL_ALL`.
- Removed `signal.mode = self.mode` assignment before publishing (the `Signal.mode` field still exists on the dataclass but is no longer set by the base class; downstream services no longer use it for routing).
- Removed `mode` from the heartbeat payload.
- Updated import to pull `SIGNAL_ALL` instead of `SIGNAL_LIVE` / `SIGNAL_SHADOW`.
- Updated docstrings and log messages to remove MODE references.

### 3. `services/executor/main.py`
- Changed subscription from `SIGNAL_LIVE` to `SIGNAL_ALL`.
- Added `_get_live_strategy_name()` — queries `db.get_all_strategies()` on every signal, iterates rows, returns the `name` of whichever strategy has `mode='live'`, or `None` if none.
- In `handle_signal()`, inserted a DB gate before any bridge call:
  - If `_get_live_strategy_name()` returns `None`: log DEBUG, return (no order).
  - If `signal.strategy != live_strategy`: log DEBUG, return (no order).
  - Only if `signal.strategy == live_strategy`: proceed to `POST /order`.
- Added `get_all_strategies` to the `shared.db` import.
- Updated docstring to reflect the new signal flow.

### 4. `services/shadow-tracker/main.py`
- Changed subscription from `ps.psubscribe(SIGNAL_SHADOW_PATTERN)` to `ps.subscribe(SIGNAL_ALL)`.
- Removed the `if sig.mode != "shadow": return` guard in `_on_signal()`. Every signal from every strategy is now recorded as a virtual trade.
- Changed message-loop branch from `msg_type == "pmessage"` to `msg_type == "message" and channel == SIGNAL_ALL`.
- Updated import to pull `SIGNAL_ALL` instead of `SIGNAL_SHADOW_PATTERN`.
- Updated docstrings explaining intent: recording the live strategy's signals too enables real-vs-virtual comparison in the dashboard.

### 5. `docker-compose.yml`
- Removed `MODE: ${EMA_SCALP_MODE:-live}` from `strategy-ema-scalp`.
- Removed `MODE: ${LIQ_SWEEP_MODE:-shadow}` from `strategy-liq-sweep`.
- Removed `MODE: ${OB_FVG_MODE:-shadow}` from `strategy-ob-fvg`.
- All other env vars are unchanged.

### 6. `.env.example`
- Removed `EMA_SCALP_MODE`, `LIQ_SWEEP_MODE`, `OB_FVG_MODE` lines.
- Added a comment block explaining mode is now DB-controlled and how to promote a strategy (dashboard button → `db.swap_live_strategy()`, or direct SQL).

---

## How the Executor Decides Whose Signal to Execute

On every message received on `signals:all`:

1. Parse the JSON into a `Signal` object — get `signal.strategy`.
2. Call `db.get_all_strategies()` — returns all rows from the `strategies` table.
3. Find the row where `mode == 'live'`. At most one row can have this value (enforced by a partial unique index in `schema.sql`).
4. If no row has `mode == 'live'`: DEBUG log, skip (do not place order).
5. If `signal.strategy != live_strategy_name`: DEBUG log, skip.
6. If `signal.strategy == live_strategy_name`: proceed to `POST /order` on the bridge.

This means the env var and the DB can never disagree — there is no env var. The DB is the single source of truth.

---

## How Promote / Demote Works

The dashboard calls `db.swap_live_strategy(new_live)` (already implemented in `shared/db.py`). That function runs atomically:

```sql
UPDATE strategies SET mode = 'shadow' WHERE mode = 'live' AND name != <new_live>;
UPDATE strategies SET mode = 'live'   WHERE name = <new_live>;
```

No container restarts are required. On the **next signal** received by the executor, `_get_live_strategy_name()` reads the updated DB row and routes accordingly. The switch takes effect within one candle period (≤5 minutes under normal market hours).

---

## Edge Cases

| Scenario | Behaviour |
|---|---|
| No strategy has `mode='live'` in DB | Executor logs DEBUG and skips every signal — no real orders placed. Safe default. |
| DB is unreachable when signal arrives | `get_all_strategies()` raises; executor catches, logs ERROR, skips signal. Same safe-skip. |
| Two strategies somehow both set to `live` (DB constraint violation) | The partial unique index in `schema.sql` prevents this at the DB level. If constraint is somehow bypassed, `_get_live_strategy_name()` returns the first `live` row found — worst case one strategy wins deterministically. |
| Strategy container crashes and restarts | No mode state is held in the container. On restart it simply resumes publishing to `signals:all`. |
| Shadow-tracker records live strategy signals | Intentional. Allows dashboard to show "real trades made vs what virtual tracking would have done" — directly comparable because both rows exist in `virtual_trades` for the same signals. |
| `signal.mode` field on the Signal dataclass | The field still exists on the dataclass for JSON compatibility with any existing logged data. Strategy base no longer sets it; downstream services no longer read it for routing decisions. |
