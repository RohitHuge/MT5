# A9 Agent Report — Dashboard Extensions

**Date:** 2026-11-20
**Scope:** Three dashboard extensions: WebSocket signal feed, intraday floating P&L on shadow positions, Redis caching for heavy queries.

---

## 1. Changes to `dashboard/app.py`

### New imports
- `asyncio`, `threading` — for the WebSocket threading bridge
- `WebSocket`, `WebSocketDisconnect` — from FastAPI
- `SIGNAL_LIVE`, `SIGNAL_SHADOW_PATTERN` — from `shared.channels`

### Extension 1 — WebSocket live signal feed (`/ws/signals`)

New function `_run_pubsub_thread(queue, loop, stop_event)`:
- Runs in a daemon background thread (not async).
- Creates its own sync `redis-py` client and calls `pubsub.subscribe(SIGNAL_LIVE)` and `pubsub.psubscribe(SIGNAL_SHADOW_PATTERN)`.
- For every `message` or `pmessage` received, calls `loop.call_soon_threadsafe(queue.put_nowait, data)` to safely hand the message off to the asyncio event loop.
- Exits when `stop_event.is_set()` (triggered on WebSocket disconnect), then unsubscribes and closes the pubsub connection.

New endpoint `@app.websocket("/ws/signals")`:
- Accepts the WebSocket, creates an `asyncio.Queue` and a `threading.Event`.
- Starts the pubsub thread.
- Loops on `asyncio.wait_for(queue.get(), timeout=30)` — on timeout sends a `{"type":"ping"}` keepalive; on message parses JSON and forwards to the client.
- On `WebSocketDisconnect` or any send error, sets `stop_event` and exits.

**Note:** `channels.py` has no `SIGNAL_ALL` constant. The two relevant channels are `SIGNAL_LIVE = "signals:live"` (direct subscribe) and `SIGNAL_SHADOW_PATTERN = "signals:shadow:*"` (pattern subscribe). Both are subscribed in the same pubsub object so all signals are captured.

### Extension 2 — Intraday floating P&L on shadow positions

Modified `GET /api/live_positions`:
- Added `is_shadow = False` flag to every real position dict.
- After fetching open virtual trades from Postgres, makes a **single** bridge call `GET /candles?symbol=XAUUSD&timeframe=M5&count=1` shared across all shadow trades (avoids N bridge calls).
- Parses the latest candle close price from either a list or a `{"candles":[...]}` dict (handles both bridge response shapes).
- For each shadow trade: `unrealised_pnl = (current_price - entry_price) * direction_sign * lots * 100.0` (XAUUSD multiplier: tick_value=1.0, point=0.01 → lots×100).
- Sets `is_shadow = True`, `current_price`, `unrealised_pnl` on each shadow dict.
- Soft-fails: if bridge is offline, `current_price` and `unrealised_pnl` are `None`.

### Extension 3 — Redis cache for `/api/compare` and `/api/strategies`

**`GET /api/compare`:**
- Cache key: `cache:compare:{days}` (e.g. `cache:compare:30`)
- TTL: 300 seconds (5 minutes)
- On hit: return `JSONResponse(json.loads(cached))` immediately — no DB queries.
- On miss: run the three DB queries, build the result, `SET cache_key result EX=300`.
- Best-effort: if Redis is down, the query runs normally.

**`GET /api/strategies`:**
- Cache key: `cache:strategies`
- TTL: 60 seconds (1 minute)
- Same hit/miss/best-effort pattern.

**Cache invalidation on mode change:**
- `POST /api/strategy/{name}/set_mode` now DELetes `cache:strategies`, `cache:compare:7`, `cache:compare:30` after a successful mode change so the next dashboard load is never stale.

---

## 2. Changes to `dashboard/templates/index.html`

### CSS additions
- `.signals-panel` / `.signals-panel-header` / `.signals-panel-title` — container chrome.
- `.ws-status.connecting/.connected/.disconnected` — colour-coded pill badge.
- `.signals-list` — scrollable `max-height: 320px` container.
- `.signal-row` with `@keyframes slideIn` entrance animation.
- `.signal-row.fading-out` — opacity transition for rows exiting at the bottom.
- `.dir-badge.dir-buy/.dir-sell` — green/red direction badges.
- `.badge-is-shadow` — small blue badge appended to strategy name in shadow table.

### HTML additions
- `<!-- Live Signals feed -->` section inserted between the equity chart and Live Positions.
- Shadow positions table gained a **Current** column and an **Unrealised P&L** column (9 columns total, up from 8).

### JavaScript additions

**`strategyColor(name)`** — deterministic per-strategy colour assignment from `STRATEGY_COLORS` palette.

**`renderSignalRow(sig)`** — renders one signal div; normalises field names (`timestamp`/`ts`/`time`, `entry_price`/`price`/`entry`, etc.) for resilience across strategy payload shapes.

**`addSignal(sig)`** — prepends a new `.signal-row` to `#signals-list`, keeps the list capped at 20 items, fades out excess rows.

**`initSignalsWebSocket()`** — connects to `ws://{host}/ws/signals`; sets `#ws-status` to Connecting/Connected/Disconnected; auto-reconnects with exponential backoff (2s → 30s cap) on close or error.

**`loadPositions()`** — updated shadow section:
- Renders `current_price` in a new column.
- Renders `unrealised_pnl` with green/red colouring.
- Appends `<span class="badge-is-shadow">SHADOW</span>` to the strategy name cell.
- `colspan` updated from 8 → 9 for empty state row.

**Init:** `initSignalsWebSocket()` called once at page load (alongside `loadAll()`).

---

## 3. How the WebSocket threading bridge works

```
Browser  ←── WebSocket ───  FastAPI async handler (ws_signals)
                                     │
                              asyncio.Queue  ← loop.call_soon_threadsafe()
                                     │
                           Background daemon thread (_run_pubsub_thread)
                                     │
                             redis-py sync pubsub
                             (subscribe + psubscribe)
                                     │
                                  Redis
```

FastAPI's async event loop cannot block on `pubsub.listen()` (it's a sync generator). The solution:

1. A `threading.Thread` runs `_run_pubsub_thread` — it owns the blocking `pubsub.listen()` loop.
2. For each message, it calls `loop.call_soon_threadsafe(queue.put_nowait, data)` which is the only thread-safe way to hand work to an asyncio event loop from outside it.
3. The async `ws_signals` handler awaits `asyncio.wait_for(queue.get(), timeout=30)` — entirely non-blocking from the event loop's perspective.
4. On disconnect the async handler sets a `threading.Event`; the sync thread checks it on each iteration and exits cleanly, unsubscribing from Redis.

---

## 4. Cache keys and TTLs

| Key | TTL | Endpoint |
|-----|-----|----------|
| `cache:strategies` | 60 s | `GET /api/strategies` |
| `cache:compare:{days}` | 300 s | `GET /api/compare?days={days}` |

Both use sync `redis-py` (`redis.from_url(REDIS_URL, decode_responses=True)`), consistent with other services.

Invalidation: `POST /api/strategy/{name}/set_mode` DELetes `cache:strategies`, `cache:compare:7`, `cache:compare:30`.

---

## 5. Known limitations

- **`SIGNAL_ALL` does not exist** in `shared/channels.py`. The WebSocket endpoint subscribes to `SIGNAL_LIVE` (direct) and `SIGNAL_SHADOW_PATTERN` (pattern) instead, which covers all published signal channels. If a future `SIGNAL_ALL` fan-out channel is added, the subscription should be updated.
- **Shadow P&L is XAUUSD-specific.** The multiplier `lots × 100` is hardcoded for XAUUSD (point=0.01, tick_value=1.0). Multi-symbol shadow trades would need a per-symbol multiplier lookup.
- **Cache invalidation is partial.** Only `days=7` and `days=30` compare cache keys are deleted on mode change; a request with an arbitrary `days` value will serve stale data until its 5-minute TTL expires.
- **One bridge candle call per `/api/live_positions` request.** If there are no open shadow trades the bridge call still fires. This is cheap but could be guarded with an early-exit check.
- **WebSocket reconnection is client-side only.** If the server restarts, existing pubsub threads are killed; the browser auto-reconnects after the backoff delay (2–30 s).
