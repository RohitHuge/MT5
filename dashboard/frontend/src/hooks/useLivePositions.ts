import { useQuery } from '@tanstack/react-query'
import { asArray, getJson } from '../lib/api'
import type { LivePositions, RealTrade, VirtualTrade } from '../types/api'

export const livePositionsQueryKey = ['live-positions'] as const

/**
 * Open real positions (bridge) + open shadow positions (Postgres).
 * `GET /api/live_positions` — polled every 10s.
 */
export function useLivePositions() {
  return useQuery({
    queryKey: livePositionsQueryKey,
    queryFn: async (): Promise<LivePositions> => {
      const payload = await getJson<unknown>('/api/live_positions')
      const envelope = (payload ?? {}) as Partial<LivePositions>
      return {
        real: asArray<RealTrade>(envelope.real),
        shadow: asArray<VirtualTrade>(envelope.shadow),
        bridge_live: envelope.bridge_live === true,
      }
    },
    refetchInterval: 10_000,
    retry: 1,
  })
}
