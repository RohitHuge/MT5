# MT5 Multi-Strategy Trading Platform — Build Plan

**Goal:** Convert the current single-strategy auto-trader into a containerised,
multi-strategy platform where one strategy runs live on the demo account while
all others run in shadow mode on the same live data, with a unified web dashboard
for comparison.

---

## Parallel Execution Map

```
Phase 0  ──────────────────────────────────────────────────► (everyone unblocked)
            │
            ▼
Phase 1A   [Track A: MT5 Bridge]
Phase 1B   [Track B: DB Schema + Docker skeleton]       ← run simultaneously
            │
            ▼
Phase 2A   [Track A: Candle Feed + Executor]
Phase 2B   [Track B: Shadow Tracker + Postgres]         ← run simultaneously
Phase 2C   [Track C: Strategy containers — existing]    ← run simultaneously
            │
            ▼
Phase 3A   [Track A: Dashboard API + UI]
Phase 3B   [Track B: OB+FVG strategy — new]             ← run simultaneously
            │
            ▼
Phase 4    [Integration, Windows Service, deployment]   ← sequential
```

**Rule:** Never start a phase until every task in the preceding phase is marked done.
Within a phase, all tracks are independent and can be assigned to separate agents.

---

## Repository Layout (target state)

```
E:\PROJECTS\MT5\
├── backtesting\              existing — do not modify
├── services\
│   ├── mt5-bridge\           Phase 1A
│   ├── candle-feed\          Phase 2A
│   ├── executor\             Phase 2A
│   ├── shadow-tracker\       Phase 2B
│   ├── strategy-ema-scalp\   Phase 2C
│   ├── strategy-liq-sweep\   Phase 2C
│   └── strategy-ob-fvg\      Phase 3B
├── dashboard\                Phase 3A
├── shared\                   Phase 0  (contracts / shared models)
├── docker-compose.yml        Phase 1B
├── docker-compose.override.yml  (local dev overrides, gitignored)
├── .env.example              Phase 1B
└── deploy\
    ├── install-bridge-service.ps1   Phase 4
    └── README.md                    Phase 4
```

---

## Phase 0 — Contracts & Foundation
**Assigned to:** single agent or lead developer  
**Blocks:** everything else  
**Estimated time:** 0.5 day

This phase defines the shared data contracts all services will use. No business
logic is written here — only models, channel names, and API specifications.

### Task 0.1 — Create shared package
- Create `shared/` directory at repo root
- Create `shared/__init__.py`
- Create `shared/models.py` with the following dataclasses (use `dataclasses` stdlib, no external deps):

```python
@dataclass
class Candle:
    symbol: str
    timeframe: str        # "M5", "M15", etc.
    timestamp: datetime   # UTC, timezone-aware
    open: float
    high: float
    low: float
    close: float
    volume: int
    spread: int           # points

@dataclass
class Signal:
    strategy: str         # "ema-scalp", "ob-fvg", etc.
    symbol: str
    direction: str        # "BUY" or "SELL"
    entry_price: float
    sl_price: float
    tp_price: float
    lots: float
    confidence: float     # 0.0 – 1.0, strategy-defined
    candle_ts: datetime   # the candle that triggered the signal
    emitted_at: datetime  # wall-clock time signal was emitted
    mode: str             # "live" or "shadow"

@dataclass
class OrderResult:
    ticket: int
    symbol: str
    direction: str
    lots: float
    fill_price: float
    sl_price: float
    tp_price: float
    filled_at: datetime
    comment: str
    error: str | None     # None = success
```

### Task 0.2 — Define Redis channel naming convention
- Create `shared/channels.py`:

```python
CANDLE_CHANNEL   = "candles:{symbol}:{timeframe}"   # e.g. candles:XAUUSD:M5
SIGNAL_LIVE      = "signals:live"
SIGNAL_SHADOW    = "signals:shadow:{strategy}"       # e.g. signals:shadow:ob-fvg
HEARTBEAT        = "heartbeat:{service}"
```

### Task 0.3 — Write MT5 Bridge REST + WebSocket API specification
- Create `services/mt5-bridge/API_SPEC.md` with the following endpoints:

```
GET  /health
     → { "status": "ok", "terminal_connected": bool, "build": str }

GET  /account
     → { "balance": float, "equity": float, "margin": float, "free_margin": float }

GET  /symbol/{symbol}
     → { "symbol": str, "point": float, "spread": int, "volume_min": float,
         "volume_max": float, "volume_step": float, "tick_value": float }

GET  /candles?symbol=XAUUSD&timeframe=M5&count=500
     → [ { "timestamp": ISO8601, "open": float, ... } ]

GET  /positions
     → [ { "ticket": int, "symbol": str, "direction": str, "lots": float,
           "open_price": float, "sl": float, "tp": float, "profit": float } ]

POST /order
     body: { "symbol": str, "direction": "BUY"|"SELL", "lots": float,
             "sl": float, "tp": float, "comment": str }
     → OrderResult JSON

POST /close/{ticket}
     → { "ticket": int, "closed": bool, "profit": float }

WS   /ws/candles?symbol=XAUUSD&timeframe=M5
     streams: Candle JSON on every new closed candle
```

### Task 0.4 — Define database schema
- Create `shared/schema.sql` with the following tables:

```sql
CREATE TABLE strategies (
    name         TEXT PRIMARY KEY,    -- "ema-scalp", "ob-fvg"
    display_name TEXT NOT NULL,
    mode         TEXT NOT NULL DEFAULT 'shadow',  -- 'live'|'shadow'|'off'
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE virtual_trades (
    id              SERIAL PRIMARY KEY,
    strategy        TEXT NOT NULL REFERENCES strategies(name),
    symbol          TEXT NOT NULL,
    direction       TEXT NOT NULL,
    entry_price     FLOAT NOT NULL,
    sl_price        FLOAT NOT NULL,
    tp_price        FLOAT NOT NULL,
    lots            FLOAT NOT NULL,
    signal_ts       TIMESTAMPTZ NOT NULL,
    opened_at       TIMESTAMPTZ NOT NULL,
    closed_at       TIMESTAMPTZ,
    close_price     FLOAT,
    outcome         TEXT,            -- 'TP'|'SL'|'OPEN'|'FORCE_CLOSE'
    gross_pnl       FLOAT,
    commission      FLOAT,
    net_pnl         FLOAT
);

CREATE TABLE real_trades (
    ticket          BIGINT PRIMARY KEY,
    strategy        TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    direction       TEXT NOT NULL,
    lots            FLOAT NOT NULL,
    open_price      FLOAT NOT NULL,
    close_price     FLOAT,
    sl_price        FLOAT,
    tp_price        FLOAT,
    opened_at       TIMESTAMPTZ NOT NULL,
    closed_at       TIMESTAMPTZ,
    gross_pnl       FLOAT,
    commission      FLOAT,
    swap            FLOAT,
    net_pnl         FLOAT
);

CREATE TABLE daily_summary (
    strategy    TEXT NOT NULL,
    date        DATE NOT NULL,
    trades      INT DEFAULT 0,
    winners     INT DEFAULT 0,
    gross_pnl   FLOAT DEFAULT 0,
    net_pnl     FLOAT DEFAULT 0,
    is_shadow   BOOLEAN NOT NULL,
    PRIMARY KEY (strategy, date, is_shadow)
);
```

### Task 0.5 — Create docker-compose skeleton
- Create `docker-compose.yml` at repo root with all service stubs (image names,
  env var placeholders, network, volume declarations). No service needs to be
  built yet — use `image: placeholder` for services not yet containerised.
- Define one Docker network: `mt5-net` (bridge driver)
- Define one named volume: `postgres-data`
- Define `.env.example` with all required env vars:

```
MT5_BRIDGE_URL=http://host.docker.internal:8000
REDIS_URL=redis://redis:6379
POSTGRES_URL=postgresql://trader:trader@postgres:5432/mt5
POSTGRES_USER=trader
POSTGRES_PASSWORD=trader
POSTGRES_DB=mt5
LOG_LEVEL=INFO
```

---

## Phase 1A — MT5 Bridge Service
**Track A**  
**Depends on:** Phase 0  
**Can run in parallel with:** Phase 1B  
**Estimated time:** 1 day

The bridge is the only Windows-native service. It wraps the existing `MT5Adapter`
in a FastAPI server and exposes the API defined in Phase 0, Task 0.3.

### Task 1A.1 — Create service directory
- Create `services/mt5-bridge/`
- Create `services/mt5-bridge/requirements.txt`:
  ```
  fastapi>=0.111
  uvicorn[standard]>=0.29
  MetaTrader5>=5.0.0
  python-dotenv>=1.0
  websockets>=12.0
  ```

### Task 1A.2 — Implement health and account endpoints
- Create `services/mt5-bridge/main.py`
- Initialise FastAPI app
- On startup: call `mt5.initialize()` using path from env var `MT5_TERMINAL_PATH`
  (default: `C:\Program Files\MetaTrader 5\terminal64.exe`)
- On shutdown: call `mt5.shutdown()`
- Implement `GET /health` — return terminal connection status and build number
- Implement `GET /account` — return balance, equity, margin, free_margin from
  `mt5.account_info()`

### Task 1A.3 — Implement symbol and candles endpoints
- Implement `GET /symbol/{symbol}` using `mt5.symbol_info()`
- Implement `GET /candles` — accept `symbol`, `timeframe`, `count` query params;
  map timeframe string to `mt5.TIMEFRAME_*` constants; return list of candle dicts
- Timeframe map must cover: M1, M5, M15, M30, H1, H4, D1

### Task 1A.4 — Implement order management endpoints
- Implement `GET /positions` — return all open positions from `mt5.positions_get()`
- Implement `POST /order` — accept Signal-compatible JSON body; build
  `mt5.order_send()` request; return OrderResult JSON; never log or print the
  account password (it is not used here — terminal handles auth)
- Implement `POST /close/{ticket}` — close an open position by ticket using a
  market order in the opposite direction

### Task 1A.5 — Implement WebSocket candle stream
- Add `WS /ws/candles` endpoint
- Accept `symbol` and `timeframe` query params
- Run a background loop: every 5 seconds poll for the latest closed candle;
  if its timestamp is newer than the last emitted candle, send it as JSON over
  the WebSocket
- Support multiple simultaneous WebSocket clients (use a connection manager)
- Reconnect gracefully if the MT5 terminal disconnects

### Task 1A.6 — Create Windows Service installer script
- Create `services/mt5-bridge/install-service.ps1`
- Use NSSM (Non-Sucking Service Manager) to register `uvicorn main:app` as a
  Windows Service named `MT5Bridge`
- Service should auto-start on boot, restart on failure
- Script must print instructions for first-time setup (install NSSM, run script
  as Administrator)
- Create `services/mt5-bridge/README.md` with setup instructions

---

## Phase 1B — Infrastructure: Database, Redis, Docker Compose
**Track B**  
**Depends on:** Phase 0  
**Can run in parallel with:** Phase 1A  
**Estimated time:** 0.5 day

### Task 1B.1 — Finalise docker-compose.yml
- Replace placeholder images with real ones for `redis` and `postgres`
- `redis`: use `redis:7-alpine`
- `postgres`: use `postgres:16-alpine`, mount `postgres-data` volume,
  set env vars from `.env`
- Add health checks to both services
- All other services remain as stubs pointing to images not yet built

### Task 1B.2 — Create database initialisation script
- Create `shared/init_db.sql` — copy schema from Task 0.4 exactly
- Add indexes:
  ```sql
  CREATE INDEX idx_virtual_trades_strategy ON virtual_trades(strategy);
  CREATE INDEX idx_virtual_trades_signal_ts ON virtual_trades(signal_ts);
  CREATE INDEX idx_real_trades_strategy ON real_trades(strategy);
  CREATE INDEX idx_daily_summary_strategy_date ON daily_summary(strategy, date);
  ```
- Mount `shared/init_db.sql` as the postgres init script in docker-compose.yml:
  ```yaml
  volumes:
    - postgres-data:/var/lib/postgresql/data
    - ./shared/init_db.sql:/docker-entrypoint-initdb.d/init.sql
  ```

### Task 1B.3 — Create shared Python DB client
- Create `shared/db.py`
- Use `psycopg2` (sync) — no ORM
- Functions needed:
  - `get_connection()` — reads `POSTGRES_URL` from env
  - `insert_virtual_trade(trade: dict) -> int` — returns new id
  - `close_virtual_trade(id: int, close_price: float, outcome: str, net_pnl: float)`
  - `upsert_real_trade(trade: dict)`
  - `upsert_daily_summary(strategy: str, date: date, is_shadow: bool, **kwargs)`
  - `get_strategy_performance(strategy: str, days: int) -> list[dict]`
  - `get_all_strategies() -> list[dict]`

### Task 1B.4 — Verify Redis and Postgres start correctly
- Run `docker-compose up redis postgres` and confirm both pass health checks
- Connect to postgres with `psql` and confirm all tables exist
- Publish and subscribe a test message via Redis CLI to confirm connectivity

---

## Phase 2A — Candle Feed + Executor Services
**Track A**  
**Depends on:** Phase 1A and Phase 1B both complete  
**Can run in parallel with:** Phase 2B, Phase 2C  
**Estimated time:** 1 day

### Task 2A.1 — Create candle-feed service
- Create `services/candle-feed/`
- `requirements.txt`: `websockets`, `redis`, `python-dotenv`, `requests`
- `main.py` logic:
  1. On startup: seed Redis with last 500 historical candles by calling
     `GET /candles?symbol=XAUUSD&timeframe=M5&count=500` on mt5-bridge; publish
     each as JSON to `candles:XAUUSD:M5` channel with field `type: "historical"`
  2. Open WebSocket to `ws://mt5-bridge-host/ws/candles?symbol=XAUUSD&timeframe=M5`
  3. For each candle received: publish to `candles:XAUUSD:M5` with `type: "live"`
  4. Reconnect with exponential backoff (1s, 2s, 4s, max 30s) on disconnect
  5. Publish heartbeat to `heartbeat:candle-feed` every 10 seconds
- Create `Dockerfile`:
  ```dockerfile
  FROM python:3.12-slim
  WORKDIR /app
  COPY requirements.txt .
  RUN pip install --no-cache-dir -r requirements.txt
  COPY . .
  CMD ["python", "main.py"]
  ```
- Add to `docker-compose.yml` with correct env vars and `extra_hosts` pointing to
  Windows host for mt5-bridge

### Task 2A.2 — Create executor service
- Create `services/executor/`
- `requirements.txt`: `redis`, `requests`, `python-dotenv`
- `main.py` logic:
  1. Subscribe to Redis `signals:live` channel
  2. For each signal received:
     a. Validate: check `direction`, `lots`, `sl_price`, `tp_price` are present
     b. Call `POST /order` on mt5-bridge
     c. On success: log ticket number and fill price; write to `real_trades` DB table
     d. On failure: log error with full signal context; do NOT retry automatically
  3. Subscribe to `candles:XAUUSD:M5` and for each live candle: call `GET /positions`
     on mt5-bridge; update `real_trades` with current profit for open positions
  4. Publish heartbeat to `heartbeat:executor` every 10 seconds
- Dockerfile: same pattern as candle-feed
- Add to `docker-compose.yml`

### Task 2A.3 — Add extra_hosts config for bridge access from containers
- In `docker-compose.yml`, all services that call mt5-bridge need:
  ```yaml
  extra_hosts:
    - "host.docker.internal:host-gateway"
  ```
- Document this in `deploy/README.md` — required for Docker Desktop on Windows

---

## Phase 2B — Shadow Tracker Service
**Track B**  
**Depends on:** Phase 1A and Phase 1B both complete  
**Can run in parallel with:** Phase 2A, Phase 2C  
**Estimated time:** 1 day

### Task 2B.1 — Create shadow-tracker service
- Create `services/shadow-tracker/`
- `requirements.txt`: `redis`, `psycopg2-binary`, `python-dotenv`
- `main.py` logic:
  1. Subscribe to all `signals:shadow:*` channels using Redis pattern subscribe
  2. For each signal received:
     a. Parse strategy name from channel name
     b. Write a new row to `virtual_trades` with `outcome = 'OPEN'`
     c. Store the signal in an in-memory dict keyed by `(strategy, entry_price, direction)`
  3. Subscribe to `candles:XAUUSD:M5`
  4. For each live candle: iterate all open virtual trades; check if candle low <=
     sl_price (SL hit) or candle high >= tp_price (TP hit) for BUY trades (reverse
     for SELL); if hit: calculate pnl, call `close_virtual_trade()`, remove from
     in-memory dict
  5. PnL calculation: `(close_price - entry_price) * lots * tick_value / point`
     — read `tick_value` and `point` from mt5-bridge `/symbol/XAUUSD` once on startup
  6. Publish heartbeat to `heartbeat:shadow-tracker` every 10 seconds
- Dockerfile: same pattern
- Add to `docker-compose.yml`

### Task 2B.2 — Daily summary aggregation
- Add a function to `shadow-tracker/main.py` that runs once per day at UTC midnight
- Aggregates all closed virtual trades for yesterday per strategy
- Calls `upsert_daily_summary()` for each strategy
- Use `threading.Timer` or `schedule` library — not a separate service

---

## Phase 2C — Strategy Container: EMA Scalp + Liquidity Sweep
**Track C**  
**Depends on:** Phase 0 complete, Phase 2A candle-feed running  
**Can run in parallel with:** Phase 2A, Phase 2B  
**Estimated time:** 1 day

Each strategy container has an identical interface:
- Reads from Redis `candles:XAUUSD:M5`
- Maintains a rolling window of the last N candles in memory
- On each new candle: runs strategy logic, emits a Signal if conditions met
- If `MODE=live`: publishes to `signals:live`
- If `MODE=shadow`: publishes to `signals:shadow:{STRATEGY_NAME}`
- If `MODE=off`: receives candles but emits nothing

### Task 2C.1 — Create strategy base class
- Create `shared/strategy_base.py`:
  ```python
  class StrategyBase:
      def __init__(self, name: str, symbol: str, window: int): ...
      def on_candle(self, candle: Candle) -> Signal | None: ...  # override this
      def run(self): ...  # connects to Redis, calls on_candle in a loop
  ```
- `run()` handles all Redis subscribe/publish logic so individual strategies only
  implement `on_candle()`

### Task 2C.2 — Create strategy-ema-scalp container
- Create `services/strategy-ema-scalp/`
- Copy signal detection logic from `backtesting/src/mt5_liquidity/strategy/scalp_ema.py`
  into a new `strategy.py` that takes a list of Candle objects and returns
  Signal | None — pure function, no MT5 imports
- `main.py`: instantiate `StrategyBase` with the ema-scalp `on_candle` function and call `run()`
- ENV vars: `MODE` (live/shadow/off), `STRATEGY_NAME=ema-scalp`, `REDIS_URL`,
  `EMA_FAST=9`, `EMA_SLOW=21`, `EMA_TREND=50`, `RSI_PERIOD=14`,
  `RSI_MIN_BUY=55`, `RSI_MAX_SELL=45`, `RISK_PCT=0.5`, `RR_TARGET=1.5`
- Dockerfile + add to docker-compose.yml

### Task 2C.3 — Create strategy-liq-sweep container
- Create `services/strategy-liq-sweep/`
- Same pattern as Task 2C.2 but wrap logic from:
  `backtesting/src/mt5_liquidity/strategy/liquidity_sweep.py`
  `backtesting/src/mt5_liquidity/strategy/sweeps.py`
  `backtesting/src/mt5_liquidity/strategy/retests.py`
- ENV vars: `MODE`, `STRATEGY_NAME=liq-sweep`, `REDIS_URL`,
  `PIVOT_LEFT=5`, `PIVOT_RIGHT=3`, `ZONE_TOLERANCE=10`,
  `RISK_PCT=0.5`, `RR_TARGET=2.0`
- Dockerfile + add to docker-compose.yml

---

## Phase 3A — Dashboard
**Track A**  
**Depends on:** Phase 2B complete (data in DB)  
**Can run in parallel with:** Phase 3B  
**Estimated time:** 1.5 days

### Task 3A.1 — Create dashboard service skeleton
- Create `dashboard/`
- `requirements.txt`: `fastapi`, `uvicorn`, `psycopg2-binary`, `python-dotenv`,
  `jinja2`
- Create `dashboard/app.py` with FastAPI app
- Create `dashboard/templates/` directory
- Dockerfile + add to docker-compose.yml on port `3000`

### Task 3A.2 — Implement API endpoints
- `GET /api/health` — check DB and Redis connectivity
- `GET /api/strategies` — list all strategies with current mode and today's P&L
- `GET /api/strategy/{name}/performance?days=30` — daily rows with trades/pnl/winrate
- `GET /api/strategy/{name}/equity_curve?days=30` — cumulative equity series for chart
- `GET /api/compare?days=30` — all strategies side by side (same date range)
- `GET /api/live_positions` — current open real + shadow positions with unrealised P&L
- `POST /api/strategy/{name}/set_mode` body: `{"mode": "live"|"shadow"|"off"}` —
  when setting to live, automatically demote current live strategy to shadow;
  write change to `strategies` table

### Task 3A.3 — Build comparison page (main dashboard)
- Create `dashboard/templates/index.html`
- Uses Chart.js (CDN, no build step)
- Layout:
  - Top bar: live strategy name + today's real P&L + account equity from mt5-bridge
  - Main table: all strategies side by side — columns: strategy name, mode badge
    (LIVE/SHADOW/OFF), win rate, profit factor, net P&L this week, net P&L this
    month, total trades
  - Equity curve chart: one line per strategy (last 30 days cumulative P&L)
  - Promote button per row: click → POST to `/api/strategy/{name}/set_mode`
- Auto-refresh every 60 seconds via JS `setInterval`

### Task 3A.4 — Build strategy detail page
- Create `dashboard/templates/strategy.html`
- URL: `/strategy/{name}`
- Shows: daily P&L bar chart (last 30 days), trade list table, win/loss breakdown
  doughnut chart, session breakdown table
- Reuses Chart.js from main page pattern

---

## Phase 3B — OB + FVG Strategy
**Track B**  
**Depends on:** Phase 0 complete, Phase 2C pattern established  
**Can run in parallel with:** Phase 3A  
**Estimated time:** 2 days

### Task 3B.1 — Implement Order Block detector
- Create `backtesting/src/mt5_liquidity/strategy/order_blocks.py`
- Function: `detect_order_blocks(candles: list[Candle], lookback: int = 20) -> list[OrderBlock]`
- `OrderBlock` dataclass: `direction` (BUY/SELL), `high`, `low`, `origin_ts`,
  `impulse_size_points`, `is_valid` (True until price closes through the OB)
- Bullish OB: last bearish candle before a bullish impulse of >= `MIN_IMPULSE_POINTS`
  (configurable, default 50 pts); impulse = sum of body sizes of the next 2 candles
- Bearish OB: last bullish candle before a bearish impulse of >= `MIN_IMPULSE_POINTS`
- OB is invalidated (set `is_valid = False`) when price closes a full candle body
  beyond its low (bullish) or high (bearish)

### Task 3B.2 — Implement FVG detector
- Create `backtesting/src/mt5_liquidity/strategy/fvg.py`
- Function: `detect_fvgs(candles: list[Candle], min_gap_points: int = 10) -> list[FVG]`
- `FVG` dataclass: `direction` (BUY/SELL), `gap_high`, `gap_low`, `origin_ts`,
  `gap_size_points`, `is_filled`
- Bullish FVG: `candle[i-1].high < candle[i+1].low` — gap between them, price
  expected to retrace into it then continue up
- Bearish FVG: `candle[i-1].low > candle[i+1].high` — reverse
- FVG is marked filled when a later candle closes inside the gap zone

### Task 3B.3 — Implement OB+FVG confluence signal
- Create `backtesting/src/mt5_liquidity/strategy/ob_fvg.py`
- Function: `get_ob_fvg_signal(candles: list[Candle], obs: list[OrderBlock], fvgs: list[FVG]) -> Signal | None`
- Logic:
  1. Take the most recent valid OB in each direction
  2. Check if any active (unfilled) FVG overlaps with that OB zone (gap_high > ob_low AND gap_low < ob_high)
  3. Check if current candle close has entered the overlap zone
  4. If yes: emit Signal
  5. SL = OB low (bullish) or OB high (bearish) minus 15pt buffer
  6. TP = SL distance × RR_TARGET
  7. Only emit one signal per OB (track emitted OBs by origin_ts)

### Task 3B.4 — Backtest OB+FVG on 3-month dataset
- Run the existing backtester CLI with the new OB+FVG strategy against the
  2026-08-13 to 2026-11-10 M5 cache
- Record: trade count, win rate, profit factor, max drawdown
- Target: 60+ trades, win rate > 50%, PF > 1.3
- Document results in `docs/ob_fvg_backtest_result.md`

### Task 3B.5 — Create strategy-ob-fvg container
- Same pattern as Task 2C.2 and 2C.3
- Wraps the signal logic from Task 3B.3
- ENV vars: `MODE`, `STRATEGY_NAME=ob-fvg`, `REDIS_URL`, `MIN_IMPULSE_POINTS=50`,
  `MIN_GAP_POINTS=10`, `OB_LOOKBACK=20`, `RISK_PCT=0.5`, `RR_TARGET=2.0`
- Dockerfile + add to docker-compose.yml

---

## Phase 4 — Integration, Hardening, Deployment
**Sequential — all phases must be complete**  
**Estimated time:** 1 day

### Task 4.1 — End-to-end smoke test (local)
- Start full stack: `docker-compose up`
- Start mt5-bridge manually: `python services/mt5-bridge/main.py`
- Confirm candle-feed receives candles from bridge WebSocket
- Confirm strategy containers publish shadow signals to Redis
- Confirm shadow-tracker writes virtual trades to postgres
- Confirm dashboard shows all strategies with P&L

### Task 4.2 — Add Docker Hub push workflow
- Create `deploy/push-images.ps1` PowerShell script
- Script builds and pushes each service image:
  ```powershell
  $services = @("candle-feed","executor","shadow-tracker",
                "strategy-ema-scalp","strategy-liq-sweep",
                "strategy-ob-fvg","dashboard")
  foreach ($s in $services) {
      docker build -t "rohithuge/mt5-$s`:latest" "services/$s"
      docker push "rohithuge/mt5-$s`:latest"
  }
  ```
- Document Docker Hub repo naming: `rohithuge/mt5-{service-name}`

### Task 4.3 — Create Windows Server deployment guide
- Create `deploy/README.md` with step-by-step instructions:
  1. Install Docker Desktop on Windows Server with WSL2 backend
  2. Clone repo
  3. Copy `.env.example` to `.env`, fill in values
  4. Install NSSM: `choco install nssm` or manual download
  5. Install MT5 Bridge as Windows Service: `.\services\mt5-bridge\install-service.ps1`
  6. Pull all images: `docker-compose pull`
  7. Start all containers: `docker-compose up -d`
  8. Verify: open `http://localhost:3000` in browser
  9. Confirm EMA Scalp strategy is set to `live` mode in dashboard

### Task 4.4 — Add automatic restart policies to docker-compose.yml
- All services: `restart: unless-stopped`
- This ensures containers survive server reboots (Docker Desktop auto-starts on login)

### Task 4.5 — Create OPERATIONS.md
- Document day-to-day operations:
  - How to check service health: `docker-compose ps`
  - How to view logs: `docker-compose logs -f strategy-ema-scalp`
  - How to promote a strategy to live (via dashboard UI)
  - How to add a new strategy (3-step guide referencing Phase 2C pattern)
  - How to update a strategy image: `docker-compose pull strategy-ob-fvg && docker-compose up -d strategy-ob-fvg`
  - How to stop trading immediately: `docker-compose stop executor`
  - MT5 Bridge: check with `Get-Service MT5Bridge` in PowerShell

---

## Parallel Assignment Guide for Agents

Use this table when assigning work to multiple agents simultaneously:

### Round 1 (after Phase 0 is done)
| Agent | Phase | Tasks |
|---|---|---|
| Agent 1 | 1A | Tasks 1A.1 → 1A.6 (MT5 Bridge) |
| Agent 2 | 1B | Tasks 1B.1 → 1B.4 (DB + Docker) |

### Round 2 (after Round 1 is done)
| Agent | Phase | Tasks |
|---|---|---|
| Agent 1 | 2A | Tasks 2A.1 → 2A.3 (Candle Feed + Executor) |
| Agent 2 | 2B | Tasks 2B.1 → 2B.2 (Shadow Tracker) |
| Agent 3 | 2C | Tasks 2C.1 → 2C.3 (Strategy containers) |

### Round 3 (after Round 2 is done)
| Agent | Phase | Tasks |
|---|---|---|
| Agent 1 | 3A | Tasks 3A.1 → 3A.4 (Dashboard) |
| Agent 2 | 3B | Tasks 3B.1 → 3B.5 (OB+FVG strategy) |

### Round 4 (after Round 3 is done)
| Agent | Phase | Tasks |
|---|---|---|
| Single agent | 4 | Tasks 4.1 → 4.5 (Integration + Deploy) |

---

## Definition of Done

A phase is complete when:
- [ ] All tasks in the phase are implemented
- [ ] The service starts without errors (`docker-compose up {service}` or native for bridge)
- [ ] The service's output is visible (logs show heartbeats, candles received, signals emitted, etc.)
- [ ] No credentials, passwords, or API keys are present in any source file or Dockerfile

The platform is ready for Monday deployment when Phase 4 Task 4.1 passes.
