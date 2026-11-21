import { useQuery } from '@tanstack/react-query'
import { asArray, getJson } from '../lib/api'
import type { Strategy } from '../types/api'

export const strategiesQueryKey = ['strategies'] as const

/**
 * All strategies with their current mode and today's rollup.
 * `GET /api/strategies` — polled every 60s (server caches for the same window).
 */
export function useStrategies() {
  return useQuery({
    queryKey: strategiesQueryKey,
    queryFn: async () => {
      const payload = await getJson<unknown>('/api/strategies')
      return asArray<Strategy>(payload)
    },
    refetchInterval: 60_000,
    retry: 1,
  })
}
