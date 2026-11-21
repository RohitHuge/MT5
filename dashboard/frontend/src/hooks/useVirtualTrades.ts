import { useQuery } from '@tanstack/react-query'
import { asArray, getJson } from '../lib/api'
import type { VirtualTradeRow, VirtualTradesPage } from '../types/api'

export const virtualTradesQueryKey = (name: string, limit: number, offset: number) =>
  ['virtual-trades', name, limit, offset] as const

/**
 * Individual virtual trades for a strategy, newest first.
 * `GET /api/strategy/:name/trades?limit=&offset=`
 *
 * The endpoint returns a `{rows, total}` envelope; `total` drives pagination.
 */
export function useVirtualTrades(
  name: string | null | undefined,
  limit: number,
  offset: number,
) {
  return useQuery({
    queryKey: virtualTradesQueryKey(name ?? '', limit, offset),
    queryFn: async (): Promise<VirtualTradesPage> => {
      const payload = await getJson<unknown>(
        `/api/strategy/${encodeURIComponent(name ?? '')}/trades?limit=${limit}&offset=${offset}`,
      )
      const rows = asArray<VirtualTradeRow>(payload)
      const total =
        payload && typeof payload === 'object' && 'total' in payload
          ? Number((payload as { total?: unknown }).total) || rows.length
          : rows.length
      return { rows, total }
    },
    enabled: Boolean(name),
    retry: 1,
  })
}
