# MT5 Bridge — REST + WebSocket API Specification

The bridge is a FastAPI application running natively on the Windows host (not in Docker).
It wraps the MetaTrader5 Python library and exposes MT5 terminal functionality over HTTP
and WebSocket so all containerised services can reach it.

**Base URL:** `http://host.docker.internal:8000` (from Docker containers)  
**Base URL:** `http://localhost:8000` (from Windows host)

---

## Authentication

None. The bridge binds to `127.0.0.1` only and relies on network-level isolation.
Never expose port 8000 to the public internet.

---

## REST Endpoints

### GET /health

Returns terminal connection status. Use this for readiness checks.

**Response 200:**
```json
{
  "status": "ok",
  "terminal_connected": true,
  "terminal_build": 6308,
  "server_time_utc": "2026-11-18T10:30:00+00:00"
}
```

**Response 503** (terminal not connected):
```json
{
  "status": "error",
  "terminal_connected": false,
  "terminal_build": null,
  "server_time_utc": "2026-11-18T10:30:00+00:00"
}
```

---

### GET /account

Returns current account balance and margin state.

**Response 200:**
```json
{
  "login": 26396767,
  "balance": 11285.00,
  "equity": 11290.50,
  "margin": 44.20,
  "free_margin": 11246.30,
  "margin_level_pct": 25542.0,
  "currency": "USD"
}
```

---

### GET /symbol/{symbol}

Returns contract specification for a symbol.

**Path param:** `symbol` — e.g. `XAUUSD`

**Response 200:**
```json
{
  "symbol": "XAUUSD",
  "point": 0.01,
  "spread": 21,
  "volume_min": 0.01,
  "volume_max": 100.0,
  "volume_step": 0.01,
  "tick_value": 1.0,
  "tick_size": 0.01,
  "contract_size": 100.0,
  "digits": 2,
  "trade_allowed": true
}
```

**Response 404** if symbol not found in terminal.

---

### GET /candles

Returns historical closed candles from MT5.

**Query params:**
- `symbol` (required) — e.g. `XAUUSD`
- `timeframe` (required) — one of: `M1 M5 M15 M30 H1 H4 D1`
- `count` (optional, default 500, max 5000) — number of candles to return

**Response 200:**
```json
[
  {
    "symbol": "XAUUSD",
    "timeframe": "M5",
    "timestamp": "2026-11-18T08:00:00+00:00",
    "open": 2648.50,
    "high": 2651.20,
    "low": 2647.80,
    "close": 2650.10,
    "volume": 1842,
    "spread": 21
  }
]
```

Candles are ordered oldest → newest. The last element is the most recent closed candle.

---

### GET /positions

Returns all currently open positions on the account.

**Response 200:**
```json
[
  {
    "ticket": 123456789,
    "symbol": "XAUUSD",
    "direction": "BUY",
    "lots": 0.02,
    "open_price": 2648.50,
    "current_price": 2651.20,
    "sl": 2638.00,
    "tp": 2663.00,
    "profit": 5.40,
    "swap": 0.0,
    "commission": -0.70,
    "opened_at": "2026-11-18T09:15:00+00:00",
    "comment": "ema-scalp"
  }
]
```

Returns `[]` when no positions are open.

---

### POST /order

Places a market order on MT5.

**Request body:**
```json
{
  "symbol": "XAUUSD",
  "direction": "BUY",
  "lots": 0.02,
  "sl": 2638.00,
  "tp": 2663.00,
  "comment": "ema-scalp"
}
```

**Fields:**
- `symbol` — trading symbol
- `direction` — `"BUY"` or `"SELL"`
- `lots` — volume (must be within symbol's min/max/step)
- `sl` — stop loss price (absolute price, not points)
- `tp` — take profit price (absolute price, not points)
- `comment` — max 31 chars, used to tag the trade in MT5 history

**Response 200 (success):**
```json
{
  "ticket": 123456790,
  "symbol": "XAUUSD",
  "direction": "BUY",
  "lots": 0.02,
  "fill_price": 2648.71,
  "sl_price": 2638.00,
  "tp_price": 2663.00,
  "filled_at": "2026-11-18T09:20:05+00:00",
  "comment": "ema-scalp",
  "error": null
}
```

**Response 200 (failure — MT5 rejected):**
```json
{
  "ticket": -1,
  "symbol": "XAUUSD",
  "direction": "BUY",
  "lots": 0.02,
  "fill_price": 0.0,
  "sl_price": 2638.00,
  "tp_price": 2663.00,
  "filled_at": "2026-11-18T09:20:05+00:00",
  "comment": "ema-scalp",
  "error": "TRADE_RETCODE_REJECT: requote"
}
```

Note: HTTP status is always 200 when the bridge itself is healthy. Check the
`error` field. Only returns HTTP 500 if the bridge crashes internally.

---

### POST /close/{ticket}

Closes an open position by ticket number at market price.

**Path param:** `ticket` — integer position ticket

**Response 200 (closed):**
```json
{
  "ticket": 123456789,
  "closed": true,
  "close_price": 2652.10,
  "profit": 7.20,
  "error": null
}
```

**Response 200 (failed to close):**
```json
{
  "ticket": 123456789,
  "closed": false,
  "close_price": 0.0,
  "profit": 0.0,
  "error": "TRADE_RETCODE_REJECT: position not found"
}
```

**Response 404:** ticket does not exist in open positions.

---

## WebSocket Endpoint

### WS /ws/candles

Streams live closed candles in real time.

**Query params:**
- `symbol` (required) — e.g. `XAUUSD`
- `timeframe` (required) — e.g. `M5`

**Connect:** `ws://host.docker.internal:8000/ws/candles?symbol=XAUUSD&timeframe=M5`

**Message format** (server → client, one JSON message per new closed candle):
```json
{
  "symbol": "XAUUSD",
  "timeframe": "M5",
  "timestamp": "2026-11-18T10:00:00+00:00",
  "open": 2650.10,
  "high": 2653.40,
  "low": 2649.20,
  "close": 2652.80,
  "volume": 2104,
  "spread": 21
}
```

**Polling interval:** Bridge polls MT5 every 5 seconds. A new message is sent only
when a candle with a timestamp newer than the last emitted one is detected. Under
normal conditions this means one message per 5-minute candle close.

**Reconnection:** Clients must implement reconnect with exponential backoff. The bridge
does not buffer missed candles — after reconnect, fetch historical candles via
`GET /candles` to fill the gap.

**Ping/pong:** Standard WebSocket keepalive. Bridge sends a ping every 30 seconds.
Clients that do not respond within 10 seconds are disconnected.

---

## Error Handling Summary

| Scenario | HTTP Status | Notes |
|---|---|---|
| Terminal connected, request OK | 200 | Normal case |
| Terminal not connected | 503 | Retry after delay |
| Symbol not found | 404 | Check symbol name |
| MT5 rejected the order | 200 | Check `error` field |
| Bridge internal error | 500 | Check bridge logs |
| Invalid request body | 422 | FastAPI validation error |

---

## Implementation Notes for Phase 1A

- Use `MetaTrader5` Python package — `import MetaTrader5 as mt5`
- Call `mt5.initialize(path=os.getenv("MT5_TERMINAL_PATH", r"C:\Program Files\MetaTrader 5\terminal64.exe"))` on startup
- Never log, print, or expose account passwords — the terminal handles authentication; the bridge only calls `mt5.initialize()` with the executable path
- Timeframe string → constant map must cover M1, M5, M15, M30, H1, H4, D1
- Run with: `uvicorn main:app --host 127.0.0.1 --port 8000`
