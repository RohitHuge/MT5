# A6 Agent Completion Report — Phase 3A: Dashboard Service

**Agent:** A6  
**Phase:** 3A (Tasks 3A.1 → 3A.4)  
**Date:** 2026-11-20  
**Status:** Complete

---

## Files Created

| File | Purpose |
|---|---|
| `dashboard/app.py` | FastAPI backend with Jinja2 templating |
| `dashboard/templates/index.html` | Comparison page (Chart.js, no build step) |
| `dashboard/templates/strategy.html` | Strategy detail page |
| `dashboard/requirements.txt` | Python dependencies |
| `dashboard/Dockerfile` | Container build (context = repo root) |
| `agents/A6_REPORT.md` | This report |

**Modified:**

| File | Change |
|---|---|
| `docker-compose.yml` | Replaced `image: rohithuge/mt5-dashboard:latest` stub with `build: { context: ., dockerfile: dashboard/Dockerfile }` |

---

## API Endpoint List

| Method | Path | Description |
|---|---|---|
| GET | `/` | Renders `index.html` (comparison page) |
| GET | `/strategy/{name}` | Renders `strategy.html` |
| GET | `/api/health` | Checks DB + Redis + bridge; returns JSON with per-service bool |
| GET | `/api/account` | Proxies `GET /account` from MT5 bridge (equity, balance, etc.) |
| GET | `/api/strategies` | All strategies with mode + today's P&L |
| GET | `/api/strategy/{name}/performance?days=30` | Daily `daily_summary` rows |
| GET | `/api/strategy/{name}/equity_curve?days=30` | Cumulative equity series |
| GET | `/api/compare?days=30` | All strategies aggregated (week + month sub-totals) |
| GET | `/api/live_positions` | Open real + open shadow positions |
| POST | `/api/strategy/{name}/set_mode` | `{"mode":"live"\|"shadow"\|"off"}` |

---

## Key Architecture Decisions

### 1. `swap_live_strategy()` is the only promotion path
`POST /api/strategy/{name}/set_mode` with `mode=live` calls `db.swap_live_strategy(name)` which is a single atomic transaction. Direct `set_strategy_mode()` is never called with mode=live from the dashboard. This is per the critical notes from prior agents.

### 2. Empty-state handling everywhere
- `daily_summary` rows are written at UTC midnight for the **previous** day, so today always has zero rows on first run. Every endpoint and every chart handles empty arrays gracefully: tables show a descriptive empty-state message, charts render blank with an explanatory label.

### 3. Bridge is optional / degraded mode
All calls to `MT5_BRIDGE_URL` are wrapped in `_bridge_get()` which catches all exceptions and returns `None`. The dashboard continues working (with `—` placeholders for equity/balance) when the bridge is offline or unreachable from the container.

### 4. Equity curve fallback
For live-mode strategies, `GET /api/strategy/{name}/equity_curve` first tries `is_shadow=False` (real trades). If that returns nothing (early deployment, no closed real trades yet), it falls back to `is_shadow=True` (shadow curve). This avoids a blank chart while the strategy accumulates real trade history.

### 5. No build step in frontend
Chart.js 4.4.3 is loaded from jsDelivr CDN. No webpack, Vite, or Node.js needed. All JS is vanilla ES2020 in `<script>` tags. This keeps the Dockerfile and CI requirements minimal.

### 6. Auto-refresh via countdown ticker
`setInterval(tickCountdown, 1000)` decrements a 60-second counter and calls `loadAll()` when it reaches zero. This matches the spec and is visible in the top bar so the trader knows when the next update is coming.

### 7. `sys.path` for shared/ inside container
The Dockerfile copies `shared/` to `/app/shared/` and `dashboard/` contents to `/app/`. The `app.py` inserts `/app` into `sys.path` at startup so `from shared import db` resolves correctly inside the container.

---

## Production Deployment Notes

1. **Environment variables required:**
   - `POSTGRES_URL` — connection string to the postgres container
   - `REDIS_URL` — `redis://redis:6379` (default)
   - `MT5_BRIDGE_URL` — `http://host.docker.internal:8000` (default; bridge is Windows-native)
   - `LOG_LEVEL` — optional, defaults to `INFO`
   - `DASHBOARD_PORT` — optional, defaults to `3000`

2. **Postgres must be healthy before dashboard starts.** The `depends_on: postgres: condition: service_healthy` in `docker-compose.yml` ensures this.

3. **Bridge connectivity.** The dashboard calls the bridge only for `/account` and `/health`. If the bridge is offline (e.g. MT5 terminal closed), the dashboard still loads and shows all DB-backed data; only the equity/balance fields show `—`.

4. **Port.** The container listens on port 3000. It is exposed as `${DASHBOARD_PORT:-3000}:3000`. Access via `http://localhost:3000` after `docker-compose up dashboard`.

5. **CDN dependency.** `index.html` and `strategy.html` load Chart.js from `cdn.jsdelivr.net`. An air-gapped deployment would need to vendor the script or use a local nginx. Add a `static/` directory and serve via `app.mount("/static", ...)` if needed.

6. **daily_summary is pre-aggregated.** The dashboard never queries `virtual_trades` or `real_trades` directly for charts — it reads `daily_summary` rows. If the shadow-tracker or executor have not yet written summary rows (new deployment), all chart areas show the empty-state placeholder until UTC midnight of the first full day.

7. **Profit Factor approximation.** The comparison table shows `winners / losers` as a proxy for profit factor (true PF requires per-trade R-multiple data). Once the platform has >30 days of history a more accurate PF query can replace this.

---

## Blockers / Notes for Lead

- **No `/api/strategy/{name}/trades` endpoint yet.** The strategy detail page shows open positions fetched from `/api/live_positions`, but closed trade history is summarised in the bar chart from `daily_summary` rows. If the lead wants a full closed-trade drill-down, a `get_recent_trades(strategy, limit)` function needs to be added to `shared/db.py` and a new endpoint added to `app.py`. The table HTML and fetch logic are already scaffolded.

- **Profit Factor column.** Currently `winners / losers` count ratio. For a true profit factor (gross profit / gross loss) the `get_comparison_summary()` query in `shared/db.py` needs to include `SUM(CASE WHEN net_pnl > 0 THEN net_pnl ELSE 0 END)` and the negative counterpart. This can be a Phase 4 polish item.

- **No authentication.** The dashboard is an internal tool on the Docker network. If the `${DASHBOARD_PORT}` is ever exposed publicly, an API key header or basic auth middleware should be added to `app.py`.
