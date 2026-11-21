export interface Account {
  login: number
  balance: number
  equity: number
  margin: number
  free_margin: number
  margin_level_pct: number
  currency: string
}

export interface HealthStatus {
  status: 'ok' | 'degraded' | 'error'
  db: boolean
  redis: boolean
  bridge: boolean
  bridge_detail?: BridgeHealth
  checked_at: string
}

export interface BridgeHealth {
  status: string
  terminal_connected: boolean
  terminal_build: number | null
  server_time_utc: string
}

export interface ApiError {
  detail: string
}

// ─────────────────────────────────────────────────────────────────────────────
// Strategies
// ─────────────────────────────────────────────────────────────────────────────

export type StrategyMode = 'live' | 'shadow' | 'off'

/** Row from `GET /api/strategies` (strategies table + today's rollup). */
export interface Strategy {
  name: string
  display_name: string
  description: string | null
  mode: StrategyMode
  today_pnl: number | null
  today_trades: number | null
  today_win_rate: number | null
  created_at?: string | null
  updated_at?: string | null
}

export interface SetModeResponse {
  strategy: string
  mode: StrategyMode
  ok: boolean
}

// ─────────────────────────────────────────────────────────────────────────────
// Performance / equity / comparison
// ─────────────────────────────────────────────────────────────────────────────

/** Row from `GET /api/strategy/:name/performance` (daily_summary). */
export interface StrategyPerfRow {
  strategy?: string
  date: string
  is_shadow?: boolean | null
  trades: number | null
  winners: number | null
  losers: number | null
  gross_pnl?: number | null
  commission?: number | null
  net_pnl: number | null
  win_rate: number | null
  updated_at?: string | null
}

/** Row from `GET /api/strategy/:name/equity_curve` (raw, values may be null). */
export interface EquityCurveRow {
  date: string
  cumulative_pnl: number | null
  net_pnl?: number | null
}

/** Chart-ready equity point. */
export interface EquityCurvePoint {
  date: string
  cumulative_pnl: number
}

/** Chart-ready daily P&L point. */
export interface DailyPnlPoint {
  date: string
  net_pnl: number
}

/** Row from `GET /api/compare`. Several aggregates can come back null. */
export interface CompareRow {
  strategy: string
  display_name: string | null
  mode: StrategyMode | null
  total_trades: number | null
  total_winners: number | null
  win_rate_pct: number | null
  total_net_pnl: number | null
  week_net_pnl: number | null
  month_net_pnl: number | null
  profit_factor: number | null
  profitable_days?: number | null
  trading_days?: number | null
}

// ─────────────────────────────────────────────────────────────────────────────
// Positions
// ─────────────────────────────────────────────────────────────────────────────

/** Row from real_trades as returned inside `/api/live_positions`. */
export interface RealTrade {
  ticket: number
  strategy: string | null
  symbol: string
  direction: string
  lots: number | null
  open_price: number | null
  close_price?: number | null
  sl_price?: number | null
  tp_price?: number | null
  opened_at?: string | null
  closed_at?: string | null
  gross_pnl?: number | null
  commission?: number | null
  swap?: number | null
  net_pnl?: number | null
  unrealised_pnl?: number | null
  is_open?: boolean
  is_shadow?: boolean
}

/** Row from virtual_trades as returned inside `/api/live_positions` (`shadow`). */
export interface VirtualTrade {
  id: number
  strategy: string
  symbol: string
  direction: string
  entry_price: number | null
  sl_price: number | null
  tp_price: number | null
  lots: number | null
  signal_ts?: string | null
  opened_at?: string | null
  closed_at?: string | null
  close_price?: number | null
  outcome?: string | null
  gross_pnl?: number | null
  commission?: number | null
  net_pnl?: number | null
  current_price?: number | null
  unrealised_pnl?: number | null
  is_shadow?: boolean
}

export interface LivePositions {
  real: RealTrade[]
  shadow: VirtualTrade[]
  bridge_live: boolean
}

/**
 * Individual virtual_trades row from `GET /api/strategy/:name/trades`.
 * Unlike `VirtualTrade` these include closed trades, so outcome, close_price
 * and realised P&L are populated.
 */
export interface VirtualTradeRow {
  id: number
  strategy: string
  symbol: string
  direction: string
  entry_price: number | null
  sl_price: number | null
  tp_price: number | null
  lots: number | null
  signal_ts: string | null
  opened_at: string | null
  closed_at: string | null
  close_price: number | null
  outcome: string
  gross_pnl: number | null
  commission: number | null
  net_pnl: number | null
}

/** `{rows, total}` envelope returned by `GET /api/strategy/:name/trades`. */
export interface VirtualTradesPage {
  rows: VirtualTradeRow[]
  total: number
}

// ─────────────────────────────────────────────────────────────────────────────
// Candles / chart
// ─────────────────────────────────────────────────────────────────────────────

/** One OHLCV bar from `GET /api/candles` (oldest → newest). */
export interface Candle {
  symbol: string
  timeframe: string
  timestamp: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  spread: number
}

/** Row from `GET /api/strategy/:name/snapshots` — indicators is strategy-shaped. */
export interface SnapshotRow {
  id: number
  strategy: string
  ts: string
  indicators: Record<string, unknown>
}

// ─────────────────────────────────────────────────────────────────────────────
// Backtest
// ─────────────────────────────────────────────────────────────────────────────

/** Row from `GET /api/backtest/history` (Phase 2-Backend, may not exist yet). */
export interface BacktestRun {
  id: number
  strategy: string
  symbol: string | null
  timeframe: string | null
  from_date: string | null
  to_date: string | null
  params?: Record<string, unknown> | null
  total_trades: number | null
  winners?: number | null
  win_rate: number | null
  net_pnl: number | null
  max_drawdown: number | null
  profit_factor: number | null
  sharpe?: number | null
  run_at: string | null
  /** Present on history rows; the type omitted it while it was unused. */
  trade_log?: BacktestTrade[] | null
}

/** Request body for `POST /api/backtest/run`. */
export interface BacktestRequest {
  strategy: string
  symbol: string
  timeframe: string
  from_date: string
  to_date: string
  params: Record<string, number>
}

/** One simulated trade in `trade_log`. */
export interface BacktestTrade {
  direction: string
  entry_price: number | null
  sl_price: number | null
  tp_price: number | null
  lots?: number | null
  close_price: number | null
  outcome: string | null
  gross_pnl?: number | null
  commission?: number | null
  net_pnl: number | null
  opened_at: string | null
  closed_at: string | null
}

/** Aggregated metrics computed server-side by `_compute_backtest_metrics`. */
export interface BacktestMetrics {
  total_trades: number
  winners: number
  win_rate: number
  net_pnl: number
  max_drawdown: number
  profit_factor: number | null
  sharpe: number | null
}

/**
 * `POST /api/backtest/run` response. The endpoint returns HTTP 200 even on
 * failure, signalling it with `ok: false` — callers must check the flag.
 */
export interface BacktestResult {
  ok: boolean
  error?: string
  bridge_live?: boolean
  run_id?: number | null
  strategy?: string
  symbol?: string
  timeframe?: string
  from_date?: string
  to_date?: string
  candles_used?: number
  metrics?: BacktestMetrics
  trade_log?: BacktestTrade[]
  total_trades?: number
  winners?: number
  win_rate?: number
  net_pnl?: number
  max_drawdown?: number
  profit_factor?: number | null
  sharpe?: number | null
}

// ─────────────────────────────────────────────────────────────────────────────
// Signal feed (WebSocket `/ws/signals`)
//
// The payload is a Redis-published Signal (shared/models.py) but the shape is
// historically loose, so every consumer must resolve field aliases.
// ─────────────────────────────────────────────────────────────────────────────

export interface RawSignal {
  type?: string
  strategy?: string
  strategy_name?: string
  direction?: string
  signal?: string
  symbol?: string
  entry_price?: number | string
  price?: number | string
  entry?: number | string
  sl?: number | string
  stop_loss?: number | string
  sl_price?: number | string
  tp?: number | string
  take_profit?: number | string
  tp_price?: number | string
  lots?: number | string
  confidence?: number | string
  timestamp?: string | number
  ts?: string | number
  time?: string | number
  candle_ts?: string | number
  emitted_at?: string | number
  mode?: string
  comment?: string
  [key: string]: unknown
}

/** Normalised signal produced by `useSignalFeed` (all aliases resolved). */
export interface SignalEvent {
  /** Client-side unique key (timestamp + strategy + direction + seq). */
  id: string
  strategy: string
  direction: 'BUY' | 'SELL' | 'UNKNOWN'
  symbol: string
  entryPrice: number | null
  sl: number | null
  tp: number | null
  lots: number | null
  /** Strategy-defined heuristic 0–1, when the strategy reports it. */
  confidence: number | null
  mode: string | null
  comment: string | null
  timestamp: string | null
  receivedAt: number
}

export type SignalConnectionStatus = 'connecting' | 'connected' | 'disconnected'
