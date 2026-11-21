import { useQuery } from '@tanstack/react-query'
import { asArray, getJson } from '../lib/api'
import type { SnapshotRow } from '../types/api'

export const snapshotsQueryKey = (name: string, limit: number) =>
  ['snapshots', name, limit] as const

/**
 * Indicator snapshots for a strategy, newest first.
 * `GET /api/strategy/:name/snapshots?limit=N`
 *
 * Returns [] until the strategy has published its first snapshot (the services
 * publish one per minute).
 */
export function useSnapshots(name: string | null | undefined, limit = 200) {
  return useQuery({
    queryKey: snapshotsQueryKey(name ?? '', limit),
    queryFn: async () => {
      const payload = await getJson<unknown>(
        `/api/strategy/${encodeURIComponent(name ?? '')}/snapshots?limit=${limit}`,
      )
      return asArray<SnapshotRow>(payload)
    },
    enabled: Boolean(name),
    retry: 1,
    staleTime: 30_000,
  })
}
