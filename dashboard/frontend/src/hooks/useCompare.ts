import { useQuery } from '@tanstack/react-query'
import { asArray, getJson } from '../lib/api'
import type { CompareRow } from '../types/api'

export const compareQueryKey = (days: number) => ['compare', days] as const

/**
 * Per-strategy aggregates over the last N days.
 * `GET /api/compare?days=N` — server caches for 5 minutes per `days` value.
 */
export function useCompare(days: number) {
  return useQuery({
    queryKey: compareQueryKey(days),
    queryFn: async () => {
      const payload = await getJson<unknown>(`/api/compare?days=${days}`)
      return asArray<CompareRow>(payload)
    },
    retry: 1,
  })
}
