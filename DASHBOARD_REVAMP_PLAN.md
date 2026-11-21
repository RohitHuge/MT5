# Dashboard Revamp Plan — Full Platform UI

**Date:** 2026-11-20  
**Goal:** Replace the current single-page Jinja2 dashboard with a full React SPA —  
six independent pages, live MT5 charts, strategy indicator overlays, shadow reports,  
backtesting, and bot control — all from a single URL the client visits.

---

## Parallel Development Structure

Phases are ordered so **Phase 2 streams run simultaneously** — backend work and  
frontend page work share zero code until Phase 3 wires them together.

```
Phase 1 ──────────────────────────────────────────────► (3 hrs, sequential)
         │
         ├── Phase 2-Frontend ──────────────────────► (1 day, parallel)
         │        Pages using existing APIs
         │
         └── Phase 2-Backend ───────────────────────► (1 day, parallel)
                  New endpoints + EA + strategy services

Phase 3 ──────────────────────────────────────────────► (1.5 days, after Phase 2)
         Advanced pages that need Phase 2 output

Phase 4 ──────────────────────────────────────────────► (half day)
         Docker integration + deploy
```

**Total: ~4 days. Phase 2 streams can run on two parallel sessions.**

---

## Tech Stack

| Layer | Choice |
|---|---|
| Framework | React 18 + Vite + TypeScript |
| Routing | React Router v6 |
| Data fetching | TanStack Query v5 (polling, caching, loading states) |
| State | Zustand (MT5 connection status, strategy modes) |
| Charts | TradingView Lightweight Charts v4 (financial OHLCV charts) |
| General charts | Recharts (equity curves, bar charts, P&L heatmap) |
| Styling | Tailwind CSS v3 + shadcn/ui component primitives |
| HTTP client | axios |
| WebSocket | Native browser WebSocket hook |

FastAPI backend in `dashboard/app.py` stays running.  
Vite builds to `dashboard/frontend/dist/`.  
FastAPI mounts `dist/` as static files at `/`.  
All `/api/*` and `/ws/*` routes stay on FastAPI.

---

## Pages and Routes

| Route | Page |
|---|---|
| `/` | Overview — MT5 status, account, all strategies summary |
| `/live` | Live Trading — live chart, real positions, bot control |
| `/strategy/:name` | Strategy Detail — indicator chart, signal history, mode toggle |
| `/shadow` | Shadow Testing — reports, equity curves, comparison |
| `/backtest` | Backtesting — run tests, view history |
| `/signals` | Signal Feed — live WebSocket stream |

---

## Phase 1 — Foundation
**~3 hours. Sequential. Everything else blocks on this.**

### Task 1.1 — Scaffold React app inside `dashboard/frontend/`

```bash
cd dashboard
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
```

Creates: `dashboard/frontend/` with `src/`, `index.html`, `vite.config.ts`, `tsconfig.json`

---

### Task 1.2 — Install all dependencies in one shot

```bash
cd dashboard/frontend
npm install \
  react-router-dom \
  @tanstack/react-query \
  zustand \
  axios \
  lightweight-charts \
  recharts \
  tailwindcss postcss autoprefixer \
  @radix-ui/react-dialog @radix-ui/react-tooltip @radix-ui/react-switch \
  clsx lucide-react
npx tailwindcss init -p
```

---

### Task 1.3 — Configure Tailwind CSS

**File:** `dashboard/frontend/tailwind.config.js`

Set `content: ["./index.html", "./src/**/*.{ts,tsx}"]`.  
Extend theme with the existing dashboard color palette:
```js
colors: {
  bg:      '#0f1117',
  surface: '#1a1d27',
  border:  '#2a2d3a',
  muted:   '#94a3b8',
  live:    '#22c55e',
  shadow:  '#3b82f6',
  off:     '#64748b',
  pos:     '#22c55e',
  neg:     '#ef4444',
  accent:  '#6366f1',
}
```

**File:** `dashboard/frontend/src/index.css`  
Add `@tailwind base; @tailwind components; @tailwind utilities;` and set `body { background: #0f1117; color: #e2e8f0; }`.

---

### Task 1.4 — Vite dev proxy

**File:** `dashboard/frontend/vite.config.ts`

Add proxy so dev server forwards `/api` and `/ws` to FastAPI running on port 8001:
```ts
server: {
  proxy: {
    '/api': 'http://localhost:8001',
    '/ws':  { target: 'ws://localhost:8001', ws: true }
  }
}
```

---

### Task 1.5 — App shell: routing + QueryClient

**File:** `dashboard/frontend/src/main.tsx`

Wrap app in `<BrowserRouter>`, `<QueryClientProvider>`, `<ReactQueryDevtools>`.

**File:** `dashboard/frontend/src/App.tsx`

Define all 6 routes using `<Routes>` + `<Route>`. Each route renders `<Layout>` wrapping the page component. Add a catch-all redirect to `/`.

---

### Task 1.6 — Layout component (sidebar + topbar)

**File:** `dashboard/frontend/src/components/Layout.tsx`

Persistent shell rendered on every page:

```
┌──────────────────────────────────────────────────────┐
│ TOPBAR: logo | MT5StatusBadge | AccountSummary        │  h-14, sticky
├─────────┬────────────────────────────────────────────┤
│ SIDEBAR │ <Outlet />                                  │
│  w-52   │                                             │
│ nav     │                                             │
│ links   │                                             │
└─────────┴────────────────────────────────────────────┘
```

Sidebar links: Overview, Live Trading, Strategies (expandable: EMA / OB+FVG / LiqSwp), Shadow, Backtest, Signals.  
Active link highlighted with `accent` color.  
Sidebar collapses to icon-only on narrow screens.

---

### Task 1.7 — Zustand store: MT5 status

**File:** `dashboard/frontend/src/store/mt5Store.ts`

```ts
interface MT5State {
  connected: boolean
  build: number | null
  account: { login: number; balance: number; equity: number; freeMargin: number; marginLevel: number; currency: string } | null
  lastChecked: Date | null
  setStatus: (data) => void
}
```

Populated by a TanStack Query polling hook (`/api/health` + `/api/account` every 30s).

---

### Task 1.8 — MT5StatusBadge component

**File:** `dashboard/frontend/src/components/MT5StatusBadge.tsx`

Reads from Zustand store. Shows:
- Green dot + "Connected" / Red dot + "Disconnected"
- Build number
- Account login

Renders in topbar. Auto-updates every 30s.

---

### Task 1.9 — AccountSummary component

**File:** `dashboard/frontend/src/components/AccountSummary.tsx`

Reads from Zustand store. Shows in topbar (right side):
`Balance: $907.10 | Equity: $907.10 | Free: $907.10 | Level: —`  
Values shown in green/red if equity above/below balance.

---

### Task 1.10 — Hook: `useAccountStatus`

**File:** `dashboard/frontend/src/hooks/useAccountStatus.ts`

TanStack Query hook:
- `GET /api/health` every 30s
- `GET /api/account` every 30s
- On success: writes to Zustand store
- On error: sets `connected: false`

---

### Task 1.11 — Update FastAPI to serve React

**File:** `dashboard/app.py`

Add at the bottom (after all API routes):
```python
from fastapi.staticfiles import StaticFiles
_dist = Path(__file__).parent / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="spa")
```

This serves React for all non-API routes. API routes registered before this mount take priority.

---

### Task 1.12 — Remove Jinja2 templates

Delete `dashboard/templates/` directory and remove `Jinja2Templates` import and the two HTML-serving routes from `app.py` (`GET /` and `GET /strategy/{name}`).

---

## Phase 2-Frontend — Pages Using Existing APIs
**~1 day. Runs in parallel with Phase 2-Backend. Zero dependency on new endpoints.**

### Task 2F.1 — Overview page

**File:** `dashboard/frontend/src/pages/Overview.tsx`

Sections:
1. **Strategy grid** — 3 cards, one per strategy. Each card: mode badge, today P&L (green/red), today trades count, win rate. Calls `GET /api/strategies` (60s poll). Click navigates to `/strategy/:name`.
2. **Weekly P&L bar chart** — Recharts BarChart. Calls `GET /api/compare?days=7`. X-axis = strategies, Y-axis = net P&L. Bars colored green/red.
3. **Account snapshot** — balance, equity, margin. Same data as topbar but larger cards with trend arrows.
4. **Last 5 signals** — static list of recent signals, refreshed every 60s via `GET /api/strategy/{name}/performance`.

---

### Task 2F.2 — Strategy mode badge component

**File:** `dashboard/frontend/src/components/StrategyModeBadge.tsx`

Pill badge: LIVE (green), SHADOW (blue), OFF (grey). Used on overview cards, strategy detail page, and sidebar.

---

### Task 2F.3 — Strategy mode toggle component

**File:** `dashboard/frontend/src/components/ModeToggle.tsx`

Dropdown: OFF → SHADOW → LIVE. Calls `POST /api/strategy/{name}/set_mode`. Shows a confirm dialog with warning text before promoting to LIVE:  
_"This will place real orders on account 26396767. Shadow mode will be disabled for all other strategies."_

---

### Task 2F.4 — Shadow Testing page

**File:** `dashboard/frontend/src/pages/ShadowTesting.tsx`

Tabs: **Overview** | **EMA Scalp** | **OB+FVG** | **Liq Sweep**

**Overview tab:**
- Comparison table: all strategies, columns = total trades / win rate / net P&L (7d / 30d / all). Calls `GET /api/compare?days=30`.
- Overlaid equity curves: Recharts LineChart, one line per strategy. Calls `GET /api/strategy/{name}/equity_curve?days=30` for each.

**Per-strategy tab:**
- Metrics row: total trades, win rate, net P&L, avg winner, avg loser, profit factor, max drawdown.
- Daily P&L bar chart: Recharts BarChart. Calls `GET /api/strategy/{name}/performance?days=30`.
- Virtual positions table: columns = direction, entry, SL, TP, opened at, outcome, P&L. Calls `GET /api/live_positions` (shadow portion).
- Trade log table: paginated, all historical virtual trades for this strategy. Date range selector.

**Date range controls:** 7d / 30d / 90d / All buttons. Updates all charts and tables.

---

### Task 2F.5 — Signal Feed page

**File:** `dashboard/frontend/src/pages/SignalFeed.tsx`

WebSocket connection to `WS /ws/signals`.  
Renders a live stream of signal cards (most recent at top, max 50 shown).  
Each card: strategy name | BUY/SELL badge | XAUUSD | entry price | SL | TP | timestamp.  
Filter bar: show all / EMA only / OB+FVG only / LiqSwp only.  
Auto-pauses on scroll-up (user is reading), resumes when scrolled to bottom.  
Connection status indicator (green dot / reconnecting spinner).

**File:** `dashboard/frontend/src/hooks/useSignalFeed.ts`

Custom hook managing WebSocket lifecycle, reconnection with exponential backoff, message parsing.

---

### Task 2F.6 — Shared chart components

**File:** `dashboard/frontend/src/components/charts/EquityCurve.tsx`

Recharts `<AreaChart>` wrapper. Props: `data: {date: string, cumulative_pnl: number}[]`, `color: string`. Used across Shadow and Overview pages.

**File:** `dashboard/frontend/src/components/charts/DailyPnlBar.tsx`

Recharts `<BarChart>` wrapper. Props: `data: {date: string, net_pnl: number}[]`. Bar color: green if positive, red if negative (using Recharts cell coloring).

**File:** `dashboard/frontend/src/components/charts/MetricsRow.tsx`

Row of stat tiles. Props: `metrics: {label: string, value: string, color?: string}[]`. Used across all strategy pages.

---

### Task 2F.7 — Shared data-fetching hooks

**File:** `dashboard/frontend/src/hooks/useStrategies.ts` — `GET /api/strategies`, 60s poll.  
**File:** `dashboard/frontend/src/hooks/useStrategyPerformance.ts` — `GET /api/strategy/:name/performance?days=N`.  
**File:** `dashboard/frontend/src/hooks/useEquityCurve.ts` — `GET /api/strategy/:name/equity_curve?days=N`.  
**File:** `dashboard/frontend/src/hooks/useCompare.ts` — `GET /api/compare?days=N`.  
**File:** `dashboard/frontend/src/hooks/useLivePositions.ts` — `GET /api/live_positions`, 10s poll.

---

## Phase 2-Backend — New Endpoints + EA + Strategy Services
**~1 day. Runs in parallel with Phase 2-Frontend.**

### Task 2B.1 — Add `backtest_runs` and `strategy_snapshots` tables

**File:** `shared/schema.sql`

```sql
CREATE TABLE IF NOT EXISTS backtest_runs (
    id            SERIAL PRIMARY KEY,
    strategy      TEXT NOT NULL,
    symbol        TEXT NOT NULL DEFAULT 'XAUUSD',
    timeframe     TEXT NOT NULL DEFAULT 'M5',
    from_date     DATE NOT NULL,
    to_date       DATE NOT NULL,
    params        JSONB NOT NULL DEFAULT '{}',
    total_trades  INT,
    winners       INT,
    win_rate      FLOAT,
    net_pnl       FLOAT,
    max_drawdown  FLOAT,
    profit_factor FLOAT,
    sharpe        FLOAT,
    trade_log     JSONB,
    run_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS strategy_snapshots (
    id          BIGSERIAL PRIMARY KEY,
    strategy    TEXT NOT NULL,
    ts          TIMESTAMPTZ NOT NULL,
    indicators  JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snaps_strategy_ts
    ON strategy_snapshots (strategy, ts DESC);
-- Prune rows older than 7 days (kept lean — only used for live chart overlays)
CREATE INDEX IF NOT EXISTS idx_snaps_ts ON strategy_snapshots (ts);
```

---

### Task 2B.2 — Add DB functions for new tables

**File:** `shared/db.py`

Add:
```python
def save_backtest_run(strategy, symbol, timeframe, from_date, to_date, params, metrics, trade_log) -> int
def get_backtest_history(strategy=None, limit=20) -> list[dict]
def save_snapshot(strategy, ts, indicators: dict) -> None
def get_snapshots(strategy, limit=200) -> list[dict]
def prune_old_snapshots(days=7) -> None
```

---

### Task 2B.3 — `GET /api/candles/bulk` endpoint

**File:** `dashboard/app.py`

Paginates bridge `/candles` endpoint in chunks of 5000 until the full date range is covered.  
Parameters: `symbol`, `timeframe`, `from_iso` (ISO date), `to_iso` (ISO date).  
Algorithm:
1. Convert from/to to timestamps
2. Loop: call `GET {bridge}/candles?symbol=X&timeframe=Y&count=5000`, parsing the oldest candle's timestamp
3. Keep fetching until oldest returned candle is before `from_iso`
4. Filter and return candles within the requested range

Returns: same format as `/candles` — list of `{symbol, timeframe, timestamp, open, high, low, close, volume, spread}`.

Also: in `services/mt5-bridge/main.py` line ~332, change `count = min(count, 5000)` to `count = min(count, 50000)` to allow bulk requests directly from the bridge when needed.

---

### Task 2B.4 — `POST /api/backtest/run` endpoint

**File:** `dashboard/app.py`

Request body:
```python
class BacktestRequest(BaseModel):
    strategy: str                    # "ema-scalp" | "ob-fvg" | "liq-sweep"
    symbol: str = "XAUUSD"
    timeframe: str = "M5"
    from_date: str                   # "2025-01-01"
    to_date: str                     # "2026-11-01"
    params: dict = {}                # strategy-specific overrides
```

Flow:
1. Fetch candles in bulk via internal call to `/api/candles/bulk`
2. `POST http://{strategy_service}:{port}/backtest` with `{candles: [...], params: {...}}`
3. Receive trade list from strategy service
4. Compute metrics: total_trades, winners, win_rate, net_pnl, max_drawdown, profit_factor, Sharpe
5. Store in `backtest_runs` table via `db.save_backtest_run()`
6. Return full result including trade_log

Strategy service internal URLs: use Docker service names `http://strategy-ema-scalp:8080/backtest` etc. Add `STRATEGY_EMA_URL`, `STRATEGY_OB_URL`, `STRATEGY_LIQ_URL` env vars.

---

### Task 2B.5 — `GET /api/backtest/history` endpoint

**File:** `dashboard/app.py`

Query params: `strategy` (optional filter), `limit` (default 20).  
Returns rows from `backtest_runs` table, ordered by `run_at DESC`.  
Used by the Backtesting page history tab.

---

### Task 2B.6 — `GET /api/trade/history` endpoint

**File:** `dashboard/app.py`

Proxies new EA `/history` endpoint (Task 2B.9).  
Calls `GET {bridge}/trade_history?count=200`.  
Falls back gracefully if EA endpoint not yet available (returns empty list with `{"bridge_live": false}`).  
Used by Strategy Detail and Live Trading pages for the closed trades panel.

---

### Task 2B.7 — `GET /api/strategy/{name}/snapshots` endpoint

**File:** `dashboard/app.py`

Returns last N indicator snapshots for a strategy from `strategy_snapshots` table.  
Query param: `limit` (default 200).  
Used by Strategy Detail page to draw EMA/RSI overlays on the chart.

---

### Task 2B.8 — `POST /api/strategies/register` endpoint

**File:** `dashboard/app.py`

Called by strategy services on startup to self-register:
```python
class RegisterRequest(BaseModel):
    name: str
    display_name: str
    description: str = ""
    mode: str = "shadow"
```

Does `INSERT INTO strategies ... ON CONFLICT (name) DO UPDATE SET display_name = ...`.  
Returns `{"registered": true, "mode": current_mode_from_db}`.  
Existing mode is preserved — register never overwrites mode (strategy doesn't get to set its own mode).

---

### Task 2B.9 — Add `/history` endpoint to `MT5Bridge.mq5`

**File:** `experts/MT5Bridge.mq5`

Add a new route handler in `RouteRequest()`:
```
GET /history?count=N
```

MQL5 implementation:
```mql5
string HandleHistory(string query)
{
   int count = 100;
   // parse count from query
   
   datetime to   = TimeCurrent();
   datetime from = to - 90 * 86400;  // last 90 days default
   
   if(!HistorySelect(from, to)) return HttpError(500, "HistorySelect failed");
   
   int total = HistoryDealsTotal();
   string items[];
   int added = 0;
   
   for(int i = total - 1; i >= 0 && added < count; i--)
   {
      ulong ticket = HistoryDealGetTicket(i);
      long entry   = HistoryDealGetInteger(ticket, DEAL_ENTRY);
      if(entry != DEAL_ENTRY_OUT) continue;  // only closed deals
      
      string item = BuildObject(new string[]{
         JsonInt("ticket",     (long)ticket),
         JsonString("symbol",  HistoryDealGetString(ticket, DEAL_SYMBOL)),
         JsonDouble("profit",  HistoryDealGetDouble(ticket, DEAL_PROFIT), 2),
         JsonDouble("volume",  HistoryDealGetDouble(ticket, DEAL_VOLUME), 2),
         JsonDouble("price",   HistoryDealGetDouble(ticket, DEAL_PRICE), 5),
         JsonInt("time",       HistoryDealGetInteger(ticket, DEAL_TIME)),
         JsonString("comment", HistoryDealGetString(ticket, DEAL_COMMENT))
      });
      ArrayResize(items, added + 1);
      items[added++] = item;
   }
   return HttpOk(BuildArray(items));
}
```

Requires VNC session to recompile (one-time, ~10 minutes). Compile with F7 in MetaEditor, verify 0 errors.

---

### Task 2B.10 — Add `POST /backtest` endpoint to each strategy service

**File:** `services/strategy-ema-scalp/main.py`  
**File:** `services/strategy-liq-sweep/main.py`  
**File:** `services/strategy-ob-fvg/main.py`

Each gets a new FastAPI endpoint alongside its existing Redis-listening loop.  
The strategy services currently run as plain Python scripts — convert the entrypoint to also serve FastAPI on port 8080.

Request body:
```python
class BacktestRequest(BaseModel):
    candles: list[dict]       # [{timestamp, open, high, low, close, volume, spread}, ...]
    params:  dict = {}        # optional parameter overrides
```

Response:
```python
class BacktestResponse(BaseModel):
    trades: list[dict]        # [{direction, entry_price, sl, tp, fill_price, close_price, outcome, gross_pnl, net_pnl, opened_at, closed_at}, ...]
```

Implementation: convert candles list to the internal `Candle` model, run the strategy's existing `_detect_last_signal` (or equivalent) in a simulation loop over all candles, simulate SL/TP fills, return trade list.

**The signal detection function is identical to the live path** — this is the key design constraint.

---

### Task 2B.11 — Add strategy snapshot publishing

**File:** `shared/strategy_base.py` (or each strategy's `main.py`)

After each signal evaluation (whether a signal was emitted or not), publish the current indicator state to Redis key `snapshot:{strategy_name}` and also save to Postgres every 60 seconds (batch, not per-candle):

```python
# After each candle evaluation:
state = {
    "ts": candle.timestamp.isoformat(),
    "strategy": self.name,
    "indicators": self._get_indicator_state()   # each strategy implements this
}
redis.set(f"snapshot:{self.name}", json.dumps(state))
```

Each strategy implements `_get_indicator_state() -> dict` returning its current indicator values:
- EMA scalp: `{"ema9": 2345.1, "ema21": 2341.2, "ema50": 2330.5, "rsi": 61.3}`
- OB+FVG: `{"ob_zones": [...], "fvg_zones": [...], "latest_signal": "BUY"}`
- Liq sweep: `{"pivot_highs": [...], "pivot_lows": [...], "setup_state": "RETEST"}`

Dashboard backend reads Redis `snapshot:{name}` for real-time overlay and Postgres `strategy_snapshots` for historical overlays.

---

### Task 2B.12 — Expose strategy services on internal Docker ports

**File:** `docker-compose.yml`

Each strategy service needs to serve HTTP (FastAPI on port 8080) alongside its Redis loop.  
Add to each strategy service (no ports mapping needed — internal Docker network only):
```yaml
strategy-ema-scalp:
  environment:
    HTTP_PORT: "8080"
```

Add `STRATEGY_EMA_URL=http://strategy-ema-scalp:8080` etc. to the dashboard service's environment block.

---

## Phase 3 — Advanced Pages
**~1.5 days. Requires Phase 1 complete and Phase 2 mostly complete.**

### Task 3.1 — TradingView Lightweight Charts wrapper component

**File:** `dashboard/frontend/src/components/charts/CandleChart.tsx`

Core reusable component used by both Live Trading and Strategy Detail pages.

Props:
```ts
interface CandleChartProps {
  symbol:      string
  timeframe:   string
  height?:     number
  overlays?:   ChartOverlay[]  // EMA lines, zone shading, signal markers
  showVolume?: boolean
}
```

Implementation:
- On mount: `GET /api/candles?symbol=X&timeframe=Y&count=200` to load initial candles
- Renders using `lightweight-charts` `createChart()` + `addCandlestickSeries()`
- Subscribes to `WS /ws/candles?symbol=X&timeframe=Y` for new closed bars → calls `series.update()`
- Polls `GET /api/symbol/{symbol}` every 5s → updates the last (forming) bar's close price on the chart
- Each `ChartOverlay` renders as a line series, area series, or marker set on top of candles

---

### Task 3.2 — Live Trading page

**File:** `dashboard/frontend/src/pages/LiveTrading.tsx`

Three sections:

**Left column (60%):**
- `<CandleChart symbol="XAUUSD" timeframe="M5" overlays={[]} showVolume={true} height={500} />`
- Timeframe switcher: M1 / M5 / M15 / H1 buttons update the chart
- Symbol switcher (future-proof): dropdown, currently only XAUUSD

**Right column (40%):**
- **Bot Control panel**: strategy mode cards with large toggle switches. Calls `POST /api/strategy/{name}/set_mode`. Confirm dialog before LIVE.
- **Open positions table**: ticket, symbol, direction, lots, entry, current price, floating P&L (green/red), SL, TP, opened at, close button. Polls `GET /api/live_positions` every 10s.
- **Manual order panel** (collapsible): BUY/SELL selector, lots input (0.01–10), SL/TP price inputs, `POST /api/order` on submit. Disabled when bridge offline.

---

### Task 3.3 — Strategy Detail page

**File:** `dashboard/frontend/src/pages/StrategyDetail.tsx`

Route: `/strategy/:name`

**Header:** strategy display name, description, mode badge, mode toggle button.

**Chart section:**
- `<CandleChart>` with overlays from `GET /api/strategy/{name}/snapshots`
- Overlay rendering per strategy:
  - **EMA scalp**: three `addLineSeries()` calls for EMA9 (fast, yellow), EMA21 (slow, orange), EMA50 (trend, red). Data from snapshots `{"ema9": ..., "ema21": ..., "ema50": ...}`.
  - **OB+FVG**: shaded rectangle series for OB zones (blue), FVG zones (purple). Data from snapshots `{"ob_zones": [{high, low, ts_start},...], "fvg_zones": [...]}`.
  - **Liq sweep**: horizontal lines at pivot highs/lows (dashed). Data from snapshots `{"pivot_highs": [...], "pivot_lows": [...]}`.
  - All strategies: BUY/SELL arrow markers at signal timestamps from `virtual_trades`.

**Metrics row:** win rate, total trades, net P&L, avg winner, avg loser, profit factor, max drawdown. Calls `GET /api/strategy/{name}/performance?days=30`.

**Signal history table:** paginated. Columns: timestamp, direction, entry, SL, TP, outcome (TP / SL / OPEN), P&L. Click a row → chart scrolls to that timestamp.

**Closed real trades panel:** calls `GET /api/trade/history`, shows only trades with `comment` matching this strategy. Falls back to virtual_trades if real trade history unavailable.

---

### Task 3.4 — Backtesting page

**File:** `dashboard/frontend/src/pages/Backtest.tsx`

**Config panel (left sidebar ~300px):**
- Strategy selector dropdown (populated from `GET /api/strategies`)
- Symbol input (default XAUUSD)
- Timeframe selector: M1 / M5 / M15 / H1
- Date range: from/to date pickers
- Parameter overrides (shown per selected strategy):
  - EMA scalp: EMA fast/slow/trend, RSI min/max, risk %, RR target
  - OB+FVG: min impulse, min gap, lookback, risk %, RR target
  - Liq sweep: pivot left/right, zone tolerance, risk %, RR target
- **Run** button → `POST /api/backtest/run`, shows loading spinner

**Results panel (main area):**
- Metrics row: total trades, win rate, net P&L, max drawdown, profit factor, Sharpe
- Equity curve: `<EquityCurve>` chart
- Trade log table: timestamp, direction, entry, fill price, close price, outcome, P&L, cumulative P&L
- Export button: downloads trade_log as CSV

**History tab:**
- Table of past runs: strategy, date range, net P&L, win rate, run date
- Click a row to load those results into the Results panel for comparison

---

## Phase 4 — Docker Integration + Deploy
**~half day. After Phase 3 is feature-complete.**

### Task 4.1 — Update `dashboard/Dockerfile`

Add Vite build stage before Python stage:

```dockerfile
# Stage 1 — build React
FROM node:20-alpine AS frontend-build
WORKDIR /app
COPY dashboard/frontend/package*.json ./
RUN npm ci
COPY dashboard/frontend/ ./
RUN npm run build
# Output: /app/dist/

# Stage 2 — Python app
FROM python:3.11-slim
WORKDIR /app
COPY dashboard/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY dashboard/ .
COPY shared/ /app/shared/
COPY --from=frontend-build /app/dist ./frontend/dist
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "3000"]
```

---

### Task 4.2 — Add health check for strategy HTTP servers

**File:** `docker-compose.yml`

Add healthcheck to each strategy service (now that they serve HTTP on 8080):
```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
  interval: 30s
  timeout: 5s
  retries: 3
```

---

### Task 4.3 — Apply new schema to production Postgres

Since the production Postgres volume already has data (init script won't re-run), apply manually:
```bash
docker exec -i <postgres-container> psql -U trader -d mt5 << 'EOF'
-- paste backtest_runs and strategy_snapshots CREATE TABLE statements
EOF
```

Document this in `DEPLOYMENT.md` under a new "Schema Migrations" section.

---

### Task 4.4 — Coolify redeploy

Push all changes to `main` branch. Coolify auto-redeploys on push.  
Monitor Coolify logs for each container coming up healthy.  
Verify:
```bash
curl https://<dashboard-domain>/api/health
# Then open the dashboard URL in browser — React SPA loads
```

---

### Task 4.5 — Compile updated MT5Bridge.mq5 on srv

The `/history` endpoint (Task 2B.9) requires recompiling the EA in MetaEditor.

```bash
# On srv — start x11vnc
x11vnc -display :99 -localhost -nopw -forever &

# On local — SSH tunnel
ssh -L 5900:127.0.0.1:5900 srv
# Connect TigerVNC to localhost:5900
```

In MT5: F4 → open MT5Bridge.mq5 → F7 to compile → 0 errors → close MetaEditor.  
Right-click EA smiley on chart → Remove → drag MT5Bridge from Navigator back onto chart.  
Verify: `curl http://localhost:8002/history?count=5`

---

## Summary of All File Changes

| File | Phase | Change |
|---|---|---|
| `dashboard/frontend/` | 1 | **NEW** — entire React app |
| `dashboard/app.py` | 1, 2B | Add static mount, 6 new endpoints, remove Jinja2 routes |
| `dashboard/Dockerfile` | 4 | Add Node build stage |
| `dashboard/templates/` | 1 | Delete |
| `shared/schema.sql` | 2B | Add `backtest_runs`, `strategy_snapshots` tables |
| `shared/db.py` | 2B | Add 5 new DB functions |
| `shared/strategy_base.py` | 2B | Add snapshot publishing hook |
| `services/strategy-ema-scalp/main.py` | 2B | Add FastAPI server + `/backtest` endpoint + `_get_indicator_state()` |
| `services/strategy-liq-sweep/main.py` | 2B | Same |
| `services/strategy-ob-fvg/main.py` | 2B | Same |
| `services/mt5-bridge/main.py` | 2B | Raise candle cap from 5000 to 50000 |
| `experts/MT5Bridge.mq5` | 2B | Add `GET /history` endpoint |
| `docker-compose.yml` | 2B, 4 | Add strategy HTTP ports, dashboard env vars, healthchecks |
| `.env` / `.env.example` | 2B | Add `STRATEGY_EMA_URL`, `STRATEGY_OB_URL`, `STRATEGY_LIQ_URL` |

---

## Bottleneck Analysis — Wine / MQL5 vs Feature

| Feature | Bottleneck? | Detail |
|---|---|---|
| Live candlestick chart | **None** | Poll `/candles` + WS `/ws/candles`. 5s latency is invisible. |
| Current price (forming bar) | **None** | Poll `/symbol/{symbol}` every 5s. |
| Account info | **None** | `/account` already works. |
| MT5 connection status | **None** | `/health` already works. |
| Strategy mode toggle | **None** | Postgres-only, no MT5 involved. |
| Shadow reports / equity curves | **None** | Postgres-only. |
| Open positions + floating P&L | **None** | `/positions` already works. |
| Place / close orders | **None** | `/order` + `/close` already work. |
| Closed real trade history | **Minor** | Requires adding `/history` to EA + VNC recompile. 1 hour of work. |
| Strategy indicator overlays | **None** | Data comes from strategy service, not MT5. |
| Backtesting with MT5 data | **None** | Paginate `/candles`, engine runs in Python. Only change is raising the 5000 cap. |
| Sub-second tick streaming | **Yes — won't fix** | EA is request/response only. Mitigation: poll every 2–3s. For M5 strategy evaluation this is invisible. If ever needed: add `/tick` endpoint, poll at 2s. |
| Multiple concurrent API calls | **Minor** | EA handles 1 request per 1s timer tick. Page load with 3 simultaneous calls takes ~3s instead of <1s. Fix if needed: lower PollMs to 200 (5 req/s), recompile once. |
| Order book depth | **Won't do** | MT5 has this via `MarketBookGet()` but it's not useful for this use case. |

**Bottom line:** Wine and the MQL5 EA are not a bottleneck for any feature in this plan except sub-second tick streaming, which the M5 strategy workflow does not need.
