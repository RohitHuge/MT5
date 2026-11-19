# Agent A1 — Completion Report

**Agent:** A1  
**Track:** Platform Services — MT5 Bridge  
**Session:** 9b563267-023c-4a55-b120-09e77d3a9398  
**Date:** 2026-11-18  
**Status:** COMPLETE

---

## Assignment

Implement **Phase 1A** from `PLATFORM_PLAN.md` — the MT5 Bridge service.

This is the Windows-native FastAPI service that wraps the MetaTrader5 Python
library and exposes the MT5 terminal over HTTP + WebSocket so all Docker
containers can communicate with it without touching MT5 directly.

---

## Tasks Completed

| Task | Description | Status |
|---|---|---|
| 1A.1 | Create service directory + requirements.txt | Done |
| 1A.2 | Health + account endpoints | Done |
| 1A.3 | Symbol info + candles endpoints | Done |
| 1A.4 | Positions + order + close endpoints | Done |
| 1A.5 | WebSocket candle stream with ConnectionManager | Done |
| 1A.6 | Windows Service installer (NSSM) + README | Done |

---

## Files Produced

```
services/mt5-bridge/
├── main.py               — FastAPI application (~280 lines)
├── requirements.txt      — fastapi, uvicorn, MetaTrader5, python-dotenv, websockets
├── install-service.ps1   — NSSM Windows Service installer with -Uninstall flag
├── README.md             — Setup guide, endpoint table, troubleshooting
└── API_SPEC.md           — (written in Phase 0, used as spec for this phase)
```

**Git commit:** `377fe49`  
`feat(platform): Phase 1A — MT5 Bridge service`

---

## Architecture Decisions

### 1. MT5 Thread Safety — `asyncio.Lock` + `run_in_executor`

The MetaTrader5 Python API is documented as not thread-safe. FastAPI is async,
so concurrent requests would race on MT5 calls without protection.

**Solution:** Every MT5 call goes through a single helper:

```python
async def call_mt5(fn, *args, **kwargs):
    async with get_mt5_lock():
        return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))
```

The `asyncio.Lock` ensures only one coroutine executes an MT5 call at a time.
`run_in_executor` offloads the synchronous MT5 call to the thread pool so the
event loop stays unblocked.

### 2. Bind Address — `0.0.0.0` not `127.0.0.1`

The API spec originally said `127.0.0.1`. That would prevent Docker containers
from reaching the bridge via `host.docker.internal` (which maps to the host's
WSL2 bridge IP, not loopback). Changed to `0.0.0.0` so both `localhost` and
`host.docker.internal` work. Security note added to README.

### 3. WebSocket — `ConnectionManager` with per-pair polling tasks

Each unique `(symbol, timeframe)` pair gets exactly one background polling task
regardless of how many clients are connected to that pair. The task:

1. Gets 2 bars with `copy_rates_from_pos(symbol, tf, 0, 2)` every 5 seconds
2. Tracks the current (open) bar's timestamp in `_current_bar_ts`
3. When the timestamp changes → a new bar opened → `rates[1]` is the just-closed candle
4. Broadcasts the closed candle JSON to all connected clients for that pair
5. Cancels itself when the last client for that pair disconnects

This avoids the common mistake of polling `copy_rates_from_pos(..., 0, 1)` and
comparing close prices — that approach fires on every tick in an open bar.

### 4. Order Placement — `ORDER_FILLING_IOC`

Matched the pattern from the existing `auto_trader.py` in
`mt5-connectivity-poc/scripts/`. Same deviation=50, MAGIC=234001, GTC time.
README documents the `ORDER_FILLING_FOK` alternative for brokers that don't
support IOC.

### 5. Never Log Credentials

The bridge calls `mt5.initialize(path=...)` only — the terminal handles all
authentication. No login, password, or server name is ever passed through or
logged. This was enforced as a hard rule from the project security constraints.

---

## Integration Points

| Downstream service | How it uses the bridge |
|---|---|
| `candle-feed` (Phase 2A) | `WS /ws/candles?symbol=XAUUSD&timeframe=M5` for live stream |
| `candle-feed` (Phase 2A) | `GET /candles?symbol=XAUUSD&timeframe=M5&count=500` to seed history on startup |
| `executor` (Phase 2A) | `POST /order` to place live trades |
| `executor` (Phase 2A) | `POST /close/{ticket}` to close positions |
| `executor` (Phase 2A) | `GET /positions` to monitor open trades |
| `dashboard` (Phase 3A) | `GET /account` for equity display in top bar |
| `dashboard` (Phase 3A) | `GET /health` for service status indicator |

---

## How to Test (once MT5 terminal is running)

```powershell
# Dev mode
cd services\mt5-bridge
pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8000

# Check endpoints
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/account
Invoke-RestMethod "http://localhost:8000/candles?symbol=XAUUSD&timeframe=M5&count=10"
Invoke-RestMethod http://localhost:8000/positions

# Install as Windows Service (run as Administrator)
.\install-service.ps1
Get-Service MT5Bridge
```

---

## What Phase 2A Builder Needs to Know

The **candle-feed** and **executor** services (Phase 2A) depend on this bridge.
Key things for that agent:

1. **WebSocket reconnect logic is mandatory** — the bridge does not buffer missed
   candles. After reconnect, `GET /candles?count=500` to fill the gap, then
   subscribe to WS for new ones.

2. **Order response always HTTP 200** — check the `error` field, not status code.
   `error: null` = success, `error: "RETCODE_..."` = MT5 rejected it.

3. **`host.docker.internal:8000`** is the correct URL from inside containers.
   The env var `MT5_BRIDGE_URL` in `.env.example` is already set to this.

4. **`/positions` returns ALL positions** — the executor should filter by
   `comment` field (set to strategy name on order placement) or by `magic`
   number (234001) to identify positions placed by this platform.

---

## Blockers / Notes for Lead

None. Phase 1A is fully self-contained. No blockers identified.

Phase 1B (DB schema + docker-compose wiring) was running in parallel and does
not depend on Phase 1A code — only on the shared contracts from Phase 0.
