# MT5 Dashboard — Frontend

React SPA for the MT5 multi-strategy trading platform. Served by the FastAPI
backend (`dashboard/app.py`) which mounts `frontend/dist/` at `/`; all data
comes from `/api/*` and `/ws/*` on the same origin.

## Stack

React 19 · Vite 8 · TypeScript 5.9 · React Router 6 · TanStack Query 5 ·
Zustand 5 · Recharts · lightweight-charts 4 · Tailwind CSS 3 · axios · lucide-react

## Development

```bash
npm install --include=dev   # NODE_ENV=production is set globally on some machines
npm run dev                 # http://localhost:5173
```

The dev server proxies `/api` and `/ws` to the dashboard backend. Override the
target with `DASHBOARD_DEV_URL` (default `http://localhost:3000`).

## Build

```bash
npm run build   # tsc -b && vite build → dist/
npm run lint    # oxlint
```

`dist/` is gitignored; Phase 4 builds it inside the Docker image.

## Layout

```
src/
  components/        Layout shell, status badge, account summary, mode toggle,
                     strategy badge, range selector, shared state views
  components/charts/ Recharts wrappers (equity curve, daily P&L, metrics row)
  hooks/             TanStack Query data hooks + signal WebSocket feed
  lib/               api client, formatting, chart colors, error helpers
  pages/             Overview · Live Trading · Strategy Detail · Shadow ·
                     Backtest · Signal Feed
  store/             Zustand MT5 connection/account store
  types/             API response types
```

## Routes

| Route | Page |
|---|---|
| `/` | Overview |
| `/live` | Live Trading |
| `/strategy/:name` | Strategy Detail |
| `/shadow` | Shadow Testing |
| `/backtest` | Backtesting |
| `/signals` | Signal Feed |
