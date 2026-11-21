import { useQuery } from '@tanstack/react-query'
import { asArray, getJson } from '../lib/api'
import { isNumber } from '../lib/format'
import type { DailyPnlPoint, StrategyPerfRow } from '../types/api'

export const strategyPerfQueryKey = (name: string, days: number) =>
  ['strategy-perf', name, days] as const

/**
 * Daily performance rows for one strategy, normalised and sorted ascending.
 * `GET /api/strategy/:name/performance?days=N` (daily_summary rows).
 */
export function useStrategyPerf(name: string | null | undefined, days: number) {
  return useQuery({
    queryKey: strategyPerfQueryKey(name ?? '', days),
    queryFn: async () => {
      const payload = await getJson<unknown>(
        `/api/strategy/${encodeURIComponent(name ?? '')}/performance?days=${days}`,
      )
      const rows = asArray<StrategyPerfRow>(payload)

      // `daily_summary` has one row per (strategy, date, is_shadow). A strategy
      // that was promoted mid-window returns *two* rows for the same date, so
      // collapse them: prefer the real (is_shadow=false) row.
      const byDate = new Map<string, StrategyPerfRow>()
      for (const row of rows) {
        if (typeof row?.date !== 'string') continue
        const existing = byDate.get(row.date)
        const isReal = row.is_shadow === false
        const existingIsReal = existing?.is_shadow === false
        if (!existing || (isReal && !existingIsReal)) byDate.set(row.date, row)
      }

      return [...byDate.values()]
        .map((row) => ({
          date: row.date,
          trades: isNumber(row.trades) ? row.trades : 0,
          winners: isNumber(row.winners) ? row.winners : 0,
          losers: isNumber(row.losers) ? row.losers : 0,
          net_pnl: isNumber(row.net_pnl) ? row.net_pnl : 0,
          win_rate: isNumber(row.win_rate) ? row.win_rate : 0,
        }))
        .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0))
    },
    enabled: Boolean(name),
    retry: 1,
  })
}

/** Chart-ready daily P&L series derived from the same query. */
export function toDailyPnl(rows: ReadonlyArray<{ date: string; net_pnl: number }>): DailyPnlPoint[] {
  return rows.map((row) => ({ date: row.date, net_pnl: row.net_pnl }))
}
