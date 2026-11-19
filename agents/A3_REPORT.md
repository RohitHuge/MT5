# Agent A3 — Completion Report

**Agent:** A3  
**Track:** Platform Services — Candle Feed + Executor  
**Phase:** 2A  
**Session:** (branch of A1 session)  
**Date:** 2026-11-18  
**Git commit:** `43d08b2`  
**Status:** COMPLETE

---

## Assignment

Implement **Phase 2A** from `PLATFORM_PLAN.md`:
- `services/candle-feed/` — live candle distribution from bridge → Redis
- `services/executor/` — live signal consumption → real MT5 orders

---

## Tasks Completed

| Task | Description | Status |
|---|---|---|
| 2A.1 | candle-feed service (main.py, requirements.txt, Dockerfile) | Done |
| 2A.2 | executor service (main.py, requirements.txt, Dockerfile) | Done |
| 2A.3 | docker-compose.yml updated with build contexts + env pass-through | Done |

---

## Files Produced

```
services/candle-feed/
├── main.py           — asyncio WS + sync Redis publish (~150 lines)
├── requirements.txt  — websockets, redis, requests, python-dotenv
└── Dockerfile        — context=repo root so shared/ is reachable

services/executor/
├── main.py           — sync pubsub loop + threading heartbeat (~220 lines)
├── requirements.txt  — redis, requests, python-dotenv, psycopg2-binary
└── Dockerfile        — context=repo root so shared/ is reachable

docker-compose.yml    — candle-feed + executor updated from stub → build:
```

---

## Architecture Decisions

### 1. Redis List + PubSub (not PubSub alone)

The plan said "publish to `candles:XAUUSD:M5` channel". PubSub alone has a
critical flaw: if a strategy container starts *after* candle-feed has already
seeded history, it misses all historical candles and starts with an empty window.

**Solution:** dual-write on every candle:
- `RPUSH candles:XAUUSD:M5:history <candle_json>` + `LTRIM … -500 -1` — capped list
- `PUBLISH candles:XAUUSD:M5 <candle_json>` — live broadcast

Strategy containers (Phase 2C) on startup do:
```python
history = redis.lrange("candles:XAUUSD:M5:history", 0, -1)
# → up to 500 candles already waiting, no need to call bridge REST directly
```

Then subscribe to PubSub for new candles. This decouples strategy startup timing
from candle-feed startup timing.

**Key for Phase 2C builders:** the history key is `candles:{symbol}:{timeframe}:history`.
Add `CANDLE_HISTORY_KEY = "candles:{symbol}:{timeframe}:history"` to `shared/channels.py`
(additive change, no breaking effects).

### 2. Build context = repo root (not service directory)

The Dockerfiles need access to `shared/` which lives outside each service directory.
Docker `COPY` cannot traverse `../`. Setting `context: .` in docker-compose solves this:

```yaml
build:
  context: .                                      # repo root
  dockerfile: services/candle-feed/Dockerfile
```

Dockerfile then:
```dockerfile
COPY shared/ ./shared/
COPY services/candle-feed/ .
```

**All future service Dockerfiles must use the same pattern.**

### 3. Reconnect seeds history every time

After every WebSocket reconnect (whether after 1s or 30s), candle-feed calls
`seed_historical()` before reconnecting the WS. This:
- Fills the gap: any candles that closed while disconnected are in the list
- Resets the history list from scratch so no duplicates accumulate

The delay-reset logic: `delay` only resets to 1 after a *successful seed* (meaning
the bridge is reachable). If the bridge is down, seed returns 0 and the delay
keeps doubling before each retry.

### 4. Executor: single PubSub, two channels

The executor subscribes to both `signals:live` AND `candles:XAUUSD:M5` in
one `pubsub.listen()` loop. It routes by channel name:

```python
if channel == SIGNAL_LIVE:    handle_signal(data)
elif channel == CANDLE_SUB:   sync_positions()
```

This is simpler than two threads sharing state. The candle channel acts as a
clock — position sync fires every ~5 minutes (once per closed M5 candle).

### 5. Executor position sync: diff-based close detection

MT5 closes positions silently (SL/TP hit). The executor detects this by:
1. Keeping `_tracked_tickets` (in-memory, ticket → trade meta)
2. On each candle: `GET /positions` → current live tickets
3. Any ticket in `_tracked_tickets` but NOT in live positions → closed by MT5
4. Mark `is_open=False` in DB, remove from `_tracked_tickets`

This correctly handles SL hits, TP hits, and manual closes from the MT5 terminal.
The close price/profit are not captured at exact close — they reflect the last
known profit from the previous sync cycle. A future `/history` endpoint on the
bridge can improve this precision.

### 6. Crash recovery: `_load_open_trades_from_db` on startup

If the executor container restarts, `_tracked_tickets` is empty. Any positions
that were open at restart would never be detected as closed because they're not
in the tracker.

On startup, executor queries `SELECT * FROM real_trades WHERE is_open = TRUE`
directly (bypassing `shared/db.py` which doesn't expose this query). This rebuilds
`_tracked_tickets` before the main loop starts.

**Note for Phase 1B / db.py:** A `get_open_real_trades()` function would be
cleaner here. Added inline for now — could be moved to `shared/db.py` in a
cleanup pass.

---

## Important: Signal comment = strategy name

When placing orders, `comment = signal.strategy[:31]`. This is the MT5 trade
comment. The bridge's `GET /positions` returns this comment in every position.

This is how the executor (and future reporting) identifies which positions belong
to this platform vs manual trades. The dashboard and shadow-tracker should filter
on this field.

---

## What Phase 2B (shadow-tracker) needs to know

1. **History key:** `candles:XAUUSD:M5:history` (Redis List) exists. Shadow-tracker
   can seed from it on startup instead of calling the bridge REST endpoint.

2. **PubSub channel:** `candles:XAUUSD:M5` — same channel executor uses. Shadow-tracker
   subscribes to this AND `signals:shadow:*` in one pubsub.

3. **Signal format:** Use `Signal.from_json(raw)` from `shared/models.py`. The
   `strategy` field on the signal is the strategy name — use it for DB writes.

---

## What Phase 2C (strategy containers) needs to know

1. **Seed pattern on startup:**
   ```python
   raw_history = redis.lrange("candles:XAUUSD:M5:history", 0, -1)
   window = [json.loads(r) for r in raw_history]  # list of candle dicts
   ```
   Or use `Candle.from_json(r)` for each. The list is capped at 500, oldest first.

2. **Live candles:** subscribe to PubSub `candles:XAUUSD:M5`. Each message is
   plain Candle JSON (no wrapper, no `type` field).

3. **Publishing signals:**
   - `MODE=live` → `redis.publish("signals:live", signal.to_json())`
   - `MODE=shadow` → `redis.publish("signals:shadow:{strategy_name}", signal.to_json())`
   - `MODE=off` → don't publish

4. **Lots calculation:** the Signal `lots` field must be populated by the strategy
   container before publishing. Executor uses it directly — no position sizing happens
   in the executor.

---

## What Phase 3A (dashboard) needs to know

1. **executor heartbeat channel:** `heartbeat:executor` (JSON: `{service, ts}`)
2. **candle-feed heartbeat channel:** `heartbeat:candle-feed`
3. `real_trades` table is kept up-to-date by executor. Dashboard can query it
   directly for today's real P&L.

---

## Blockers / Notes

None blocking Phase 2B or 2C. Both can proceed immediately.

One cleanup item for the shared package: add
```python
CANDLE_HISTORY_KEY = "candles:{symbol}:{timeframe}:history"
```
to `shared/channels.py`. Phase 2C builders need this key — currently documented
here but not in the shared constants file.
