# MQL5 EA Migration Plan — Remove Python MT5 Package Dependency

**Date:** 2026-11-20  
**Goal:** Replace the `MetaTrader5` Python package (Windows IPC, cannot run on Linux) with a
MQL5 Expert Advisor that runs a lightweight HTTP server inside MT5 terminal. The mt5-bridge
FastAPI service remains identical externally — only its internal implementation changes.

---

## Why This Migration

The `MetaTrader5` Python package communicates with the MT5 terminal via Windows named pipes.
This is a Windows kernel feature and cannot run natively on Linux — not even with Wine, because
recent MT5 builds (2025+) have aggressive anti-tamper checks that block the terminal from
starting inside Wine entirely.

MetaQuotes officially supports MT5 on Ubuntu via their own native package. The correct
integration path for server-side use is an MQL5 Expert Advisor with a socket server — this is
what institutional integrations use and what MetaQuotes themselves recommend.

---

## Architecture Before and After

```
─── BEFORE (Windows only) ────────────────────────────────────────────────────

  Strategy containers
       │ HTTP
       ▼
  mt5-bridge (FastAPI, Windows Service)
       │ Windows Named Pipes (IPC)
       ▼
  MT5 terminal (Windows)


─── AFTER (runs anywhere) ────────────────────────────────────────────────────

  Strategy containers
       │ HTTP  (unchanged)
       ▼
  mt5-bridge (FastAPI, Docker on Ubuntu)
       │ HTTP to localhost:8002
       ▼
  MQL5 EA — MT5Bridge.mq5  (socket server inside MT5 terminal)
       │ MT5 native API
       ▼
  MT5 terminal  (Ubuntu official package OR Windows — either works)
```

**What does not change:** every strategy container, the dashboard, Redis, Postgres,
the executor, the shadow-tracker, `docker-compose.yml`, and the entire HTTP API that
everything calls. The external contract of mt5-bridge is 100% identical.

---

## Capability Mapping (Python pkg → MQL5 EA)

| Python call | Used for | MQL5 equivalent | Notes |
|---|---|---|---|
| `mt5.initialize()` | Connect at startup | Not needed | EA runs permanently inside MT5 |
| `mt5.shutdown()` | Disconnect on exit | Not needed | — |
| `mt5.terminal_info()` | Build, connected status | `TerminalInfoInteger/String()` | Identical data |
| `mt5.account_info()` | Balance, equity, margin | `AccountInfoDouble/String/Integer()` | Identical data |
| `mt5.symbol_info(s)` | Point, spread, lot sizes | `SymbolInfoDouble/Integer/String()` | Identical data |
| `mt5.symbol_info_tick(s)` | Live ask/bid for entry | `SymbolInfoTick()` | Identical data |
| `mt5.copy_rates_from_pos()` | Historical OHLCV candles | `CopyRates()` | Identical data |
| `mt5.positions_get()` | All open positions | `PositionsTotal()` + `PositionGet*()` | Identical data |
| `mt5.positions_get(ticket=x)` | Specific position | `PositionSelectByTicket()` | Identical data |
| `mt5.order_send(request)` | Place / close orders | `OrderSend()` | Same struct fields |
| `mt5.last_error()` | Error detail | `GetLastError()` | Identical |
| All constants | Order types, timeframes | Same numeric values in MQL5 | Identical |

**Nothing is lost.** Every piece of data the platform uses has a direct MQL5 native equivalent.

---

## Phase 1 — Write the MQL5 Expert Advisor

### Task 1.1 — Create `experts/MT5Bridge.mq5`

Write an MQL5 EA that opens a TCP server on port 8002 and handles HTTP/1.1 requests.

**File:** `experts/MT5Bridge.mq5`

**Endpoints to implement:**

| Method | Path | MQL5 functions | Response |
|---|---|---|---|
| GET | `/health` | `TerminalInfoInteger(TERMINAL_CONNECTED)`, `TerminalInfoInteger(TERMINAL_BUILD)` | `{status, terminal_connected, terminal_build, server_time_utc}` |
| GET | `/account` | `AccountInfoDouble`, `AccountInfoString`, `AccountInfoInteger` | `{login, balance, equity, margin, free_margin, margin_level_pct, currency}` |
| GET | `/symbol/{symbol}` | `SymbolInfoDouble`, `SymbolInfoInteger`, `SymbolInfoString` | `{symbol, point, spread, volume_min, volume_max, volume_step, tick_value, tick_size, contract_size, digits, trade_allowed}` |
| GET | `/candles?symbol=X&timeframe=Y&count=N` | `CopyRates()` | JSON array of `{symbol, timeframe, timestamp, open, high, low, close, volume, spread}` |
| GET | `/positions` | `PositionsTotal()`, `PositionGetDouble/String/Integer/Ticket()` | JSON array of all open positions |
| POST | `/order` | `SymbolInfoTick()`, `OrderSend()` | `{ticket, symbol, direction, lots, fill_price, sl_price, tp_price, filled_at, error}` |
| POST | `/close/{ticket}` | `PositionSelectByTicket()`, `SymbolInfoTick()`, `OrderSend()` | `{ticket, closed, close_price, profit, error}` |

**Implementation notes:**
- Use `ServerSocket` class (MQL5 built-in) to listen on 0.0.0.0:8002
- Parse HTTP request line to extract method + path + query string
- Serialize all responses as JSON (manual string building — no JSON library in MQL5)
- One client connection per request (HTTP/1.0 style — no keep-alive needed)
- Magic number for all orders: `234001` (matches existing platform constant)
- Order filling mode: `ORDER_FILLING_IOC` (matches existing bridge)
- Deviation: `50` points (matches existing bridge)
- Run `OnTimer()` every 100ms to accept and process incoming connections
- Input parameter: `int Port = 8002` (configurable without recompile)

### Task 1.2 — Timeframe string → MQL5 constant map

Implement a helper function that converts `"M1"`, `"M5"`, `"M15"`, `"M30"`, `"H1"`, `"H4"`, `"D1"`
to the corresponding `ENUM_TIMEFRAMES` values (`PERIOD_M1`, etc.).

### Task 1.3 — JSON serialization helpers

Write helper functions:
- `JsonString(string key, string val)` → `"key":"val"`
- `JsonDouble(string key, double val, int digits)` → `"key":1.23456`
- `JsonInt(string key, long val)` → `"key":123`
- `JsonBool(string key, bool val)` → `"key":true`
- `BuildObject(string fields[])` → `{field1,field2,...}`
- `BuildArray(string items[])` → `[item1,item2,...]`

### Task 1.4 — HTTP request parser

Write a minimal HTTP parser:
- Extract method (`GET` / `POST`)
- Extract path (e.g. `/candles`)
- Extract query params (e.g. `symbol=XAUUSD&timeframe=M5&count=500`)
- Extract POST body (read `Content-Length` header, then body)
- Return 400 for malformed requests

### Task 1.5 — Test the EA manually

1. Compile `MT5Bridge.mq5` in MetaEditor (zero errors, zero warnings)
2. Attach to any chart in MT5 terminal on Windows
3. Verify port 8002 is listening: `netstat -an | findstr 8002`
4. Test all endpoints with `curl` or Postman:
   ```
   curl http://localhost:8002/health
   curl http://localhost:8002/account
   curl http://localhost:8002/symbol/XAUUSD
   curl "http://localhost:8002/candles?symbol=XAUUSD&timeframe=M5&count=10"
   curl http://localhost:8002/positions
   ```
5. Place a test order on the demo account and verify ticket returned
6. Close the position and verify closed=true

---

## Phase 2 — Update mt5-bridge

### Task 2.1 — Replace MT5 Python calls with HTTP client

**File:** `services/mt5-bridge/main.py`

Replace:
```python
import MetaTrader5 as mt5
```

With:
```python
import httpx

EA_URL = os.getenv("EA_URL", "http://localhost:8002")
_ea_client: httpx.AsyncClient | None = None

async def call_ea(method: str, path: str, **kwargs):
    return await _ea_client.request(method, f"{EA_URL}{path}", **kwargs)
```

Update the `lifespan` function:
- Remove `mt5.initialize()` and `mt5.shutdown()`
- Create and close `httpx.AsyncClient` instead
- Call `GET /health` on startup to confirm EA is reachable

Update every endpoint handler to call the EA via HTTP instead of calling `mt5.*` functions directly.
The response shapes from the EA are designed to match the existing Python responses exactly, so
the endpoint return values stay byte-for-byte identical.

**Endpoints that change internally (external API unchanged):**
- `GET /health` → `call_ea("GET", "/health")`
- `GET /account` → `call_ea("GET", "/account")`
- `GET /symbol/{symbol}` → `call_ea("GET", f"/symbol/{symbol}")`
- `GET /candles` → `call_ea("GET", f"/candles?symbol={symbol}&timeframe={timeframe}&count={count}")`
- `GET /positions` → `call_ea("GET", "/positions")`
- `POST /order` → `call_ea("POST", "/order", json=req.dict())`
- `POST /close/{ticket}` → `call_ea("POST", f"/close/{ticket}")`

The WebSocket candle feed (`WS /ws/candles`) already polls via `call_mt5()` — replace that
single internal call with `call_ea("GET", "/candles?...")`. No other WebSocket logic changes.

### Task 2.2 — Update `services/mt5-bridge/requirements.txt`

Remove:
```
MetaTrader5
mt5linux==0.1.9
rpyc==5.0.1
```

Add:
```
httpx>=0.27.0
```

### Task 2.3 — Update `services/mt5-bridge/Dockerfile`

Remove any Windows-only build steps. The service is now pure Python and builds on Linux.

### Task 2.4 — Add `EA_URL` to `.env` and `.env.example`

```
EA_URL=http://host.docker.internal:8002
```

`host.docker.internal` resolves to the Windows/Linux host from inside Docker — the EA running
in MT5 terminal on the host will be reachable at this address.

---

## Phase 3 — Docker Integration

### Task 3.1 — Move mt5-bridge into `docker-compose.yml`

Remove the header comment "mt5-bridge is NOT here — it runs as a Windows Service."

Add `mt5-bridge` as a proper Docker service:
```yaml
  mt5-bridge:
    <<: *common
    build:
      context: .
      dockerfile: services/mt5-bridge/Dockerfile
    container_name: mt5-bridge
    ports:
      - "8000:8000"
    environment:
      EA_URL: ${EA_URL}
      LOG_LEVEL: ${LOG_LEVEL}
    profiles: ["platform"]
```

### Task 3.2 — Update `MT5_BRIDGE_URL` in `.env`

```
MT5_BRIDGE_URL=http://mt5-bridge:8000
```

All other containers already use `${MT5_BRIDGE_URL}` — no changes needed in strategy containers,
executor, shadow-tracker, or dashboard.

### Task 3.3 — Remove Windows Service install scripts

Delete or archive `services/mt5-bridge/install-service.ps1` — no longer needed.

---

## Phase 4 — MT5 Terminal on Ubuntu (Optional Upgrade)

This phase moves the MT5 terminal itself to the Ubuntu server using the official MetaQuotes
Linux package. Skip this if keeping MT5 on Windows is acceptable — the EA approach works
regardless of where MT5 terminal runs.

### Task 4.1 — Install official MT5 Linux package on Ubuntu server

```bash
wget https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.sh
chmod +x mt5setup.sh
./mt5setup.sh
```

MetaQuotes ships their own Wine configuration bundled in this installer. It bypasses all
anti-tamper checks because they patch their own Wine build. No Docker, no custom Wine.

### Task 4.2 — Log into Vantage Markets account via MT5 GUI

One-time manual step. Same as the Windows login — `File → Login to Trade Account →
26396767 → password → server → Save password`.

### Task 4.3 — Copy `MT5Bridge.mq5` to the Linux MT5 instance

```bash
cp experts/MT5Bridge.mq5 ~/.mt5/MQL5/Experts/
```

Compile and attach to a chart in the MT5 GUI.

### Task 4.4 — Update `EA_URL` in `.env` for Linux MT5

If MT5 runs on the same Ubuntu server as Docker:
```
EA_URL=http://host.docker.internal:8002
```
No change needed — `host.docker.internal` already resolves to the Ubuntu host.

### Task 4.5 — Remove the mt5-test Docker container from the server

The `gmag11/metatrader5_vnc` container used during investigation is no longer needed.
```bash
docker stop mt5-test && docker rm mt5-test
docker rmi gmag11/metatrader5_vnc:latest
docker volume rm <mt5-test-config-volume-id>
```

---

## Phase 5 — Validation

### Task 5.1 — End-to-end smoke test

With the EA running in MT5 and mt5-bridge running in Docker:

```bash
# Health check
curl http://localhost:8000/health

# Account (should show login 26396767)
curl http://localhost:8000/account

# Symbol info
curl http://localhost:8000/symbol/XAUUSD

# 10 candles
curl "http://localhost:8000/candles?symbol=XAUUSD&timeframe=M5&count=10"

# Open positions
curl http://localhost:8000/positions
```

### Task 5.2 — Start full platform stack and verify

```bash
docker-compose --profile platform up -d
```

Check all 7 containers healthy. Verify candle-feed is streaming via Redis:
```bash
docker exec mt5-redis redis-cli subscribe candles:XAUUSD:M5
```

### Task 5.3 — Place and close a test order

```bash
curl -X POST http://localhost:8000/order \
  -H "Content-Type: application/json" \
  -d '{"symbol":"XAUUSD","direction":"BUY","lots":0.01,"sl":1800.0,"tp":2000.0}'
```

Verify ticket appears in MT5 terminal and in `GET /positions`. Then close it.

### Task 5.4 — Verify strategy signals flow end-to-end

Confirm EMA-scalp strategy generates signals and executor picks them up without error.
Check `shadow_signals` table in Postgres has new rows from liq-sweep and ob-fvg.

---

## Summary of File Changes

| File | Change |
|---|---|
| `experts/MT5Bridge.mq5` | **NEW** — MQL5 EA, ~350 lines |
| `services/mt5-bridge/main.py` | Replace ~50 lines of `mt5.*` calls with `httpx` calls |
| `services/mt5-bridge/requirements.txt` | Remove MetaTrader5/mt5linux/rpyc, add httpx |
| `services/mt5-bridge/Dockerfile` | Remove Windows-specific steps |
| `docker-compose.yml` | Add mt5-bridge service, remove Windows note |
| `.env` / `.env.example` | Add `EA_URL`, update `MT5_BRIDGE_URL` |
| `services/mt5-bridge/install-service.ps1` | Archive or delete |
| All strategy containers | **No changes** |
| Dashboard | **No changes** |
| Redis / Postgres / executor / shadow-tracker | **No changes** |

---

## Effort Estimate

| Phase | Task | Estimate |
|---|---|---|
| 1 | Write MQL5 EA | 1 day |
| 2 | Update mt5-bridge | 2 hours |
| 3 | Docker integration | 1 hour |
| 4 | Ubuntu MT5 install (optional) | 30 min |
| 5 | Validation | 1 hour |
| **Total** | | **~2 days** |
