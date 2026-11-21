import { useQuery, type UseQueryOptions } from '@tanstack/react-query'
import { asArray, getJson } from '../lib/api'
import { isNumber } from '../lib/format'
import type { EquityCurvePoint, EquityCurveRow } from '../types/api'

export const equityCurveQueryKey = (name: string, days: number) =>
  ['equity-curve', name, days] as const

async function fetchEquityCurve(name: string, days: number): Promise<EquityCurvePoint[]> {
  const payload = await getJson<unknown>(
    `/api/strategy/${encodeURIComponent(name)}/equity_curve?days=${days}`,
  )
  const rows = asArray<EquityCurveRow>(payload)
    .filter((row) => typeof row?.date === 'string')
    .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0))

  // Sparse rows can carry `cumulative_pnl: null`; carry the previous value
  // forward instead of punching holes into the chart.
  let running = 0
  return rows.map((row) => {
    if (isNumber(row.cumulative_pnl)) {
      running = row.cumulative_pnl
    } else if (isNumber(row.net_pnl)) {
      running += row.net_pnl
    }
    return { date: row.date, cumulative_pnl: running }
  })
}

/**
 * Shared query options so pages that need a *variable number* of curves can use
 * `useQueries` while single-strategy views keep `useEquityCurve`. Both share the
 * same cache entries.
 */
export function equityCurveQueryOptions(
  name: string,
  days: number,
): UseQueryOptions<EquityCurvePoint[], Error> {
  return {
    queryKey: equityCurveQueryKey(name, days),
    queryFn: () => fetchEquityCurve(name, days),
    staleTime: 30_000,
    retry: 1,
  }
}

/**
 * Cumulative P&L series for one strategy.
 * `GET /api/strategy/:name/equity_curve?days=N`
 */
export function useEquityCurve(name: string | null | undefined, days: number) {
  return useQuery({
    ...equityCurveQueryOptions(name ?? '', days),
    enabled: Boolean(name),
  })
}
