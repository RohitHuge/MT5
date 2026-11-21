import { isNumber } from './format'
import type { EquityCurvePoint } from '../types/api'

export const RANGE_OPTIONS = [7, 30, 90] as const
export type RangeDays = (typeof RANGE_OPTIONS)[number]

export interface PerfRowLike {
  date: string
  trades: number
  winners: number
  losers: number
  net_pnl: number
  win_rate: number
}

export interface AggregateMetrics {
  totalTrades: number
  totalWinners: number
  totalLosers: number
  winRatePct: number | null
  netPnl: number
  profitFactor: number | null
  maxDrawdown: number
  tradingDays: number
  hasData: boolean
}

const EMPTY_METRICS: AggregateMetrics = {
  totalTrades: 0,
  totalWinners: 0,
  totalLosers: 0,
  winRatePct: null,
  netPnl: 0,
  profitFactor: null,
  maxDrawdown: 0,
  tradingDays: 0,
  hasData: false,
}

/**
 * Aggregate daily rows into headline metrics.
 *
 * The dashboard only exposes daily rollups (no trade log in Phase 2), so
 * profit factor is approximated from winners:losers — the same proxy the
 * backend uses in `/api/compare` — and max drawdown is derived from the
 * running cumulative P&L of the same rows.
 */
export function aggregatePerf(rows: ReadonlyArray<PerfRowLike>): AggregateMetrics {
  if (rows.length === 0) return EMPTY_METRICS

  let trades = 0
  let winners = 0
  let losers = 0
  let netPnl = 0
  let peak = 0
  let cumulative = 0
  let maxDrawdown = 0
  let hasTrades = false

  for (const row of rows) {
    const rowTrades = isNumber(row.trades) ? row.trades : 0
    const rowWinners = isNumber(row.winners) ? row.winners : 0
    const rowLosers = isNumber(row.losers) ? Math.max(row.losers, rowTrades - rowWinners) : rowTrades - rowWinners
    const rowNet = isNumber(row.net_pnl) ? row.net_pnl : 0

    trades += rowTrades
    winners += rowWinners
    losers += Math.max(rowLosers, 0)
    netPnl += rowNet
    if (rowTrades > 0) hasTrades = true

    cumulative += rowNet
    if (cumulative > peak) peak = cumulative
    const drawdown = peak - cumulative
    if (drawdown > maxDrawdown) maxDrawdown = drawdown
  }

  const winRatePct = trades > 0 ? (winners / trades) * 100 : null
  const profitFactor = losers > 0 && winners > 0 ? winners / losers : null

  return {
    totalTrades: trades,
    totalWinners: winners,
    totalLosers: losers,
    winRatePct,
    netPnl,
    profitFactor,
    maxDrawdown,
    tradingDays: rows.length,
    hasData: hasTrades,
  }
}

/** Max drawdown of an already-cumulative curve. */
export function maxDrawdownOf(curve: ReadonlyArray<EquityCurvePoint>): number {
  let peak = 0
  let worst = 0
  for (const point of curve) {
    if (point.cumulative_pnl > peak) peak = point.cumulative_pnl
    const drawdown = peak - point.cumulative_pnl
    if (drawdown > worst) worst = drawdown
  }
  return worst
}
