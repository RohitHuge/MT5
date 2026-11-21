import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getJson } from '../lib/api'
import { useMT5Store } from '../store/mt5Store'
import type { Account, HealthStatus } from '../types/api'

const POLL_MS = 30_000

export function useAccountStatus() {
  const setStatus = useMT5Store((s) => s.setStatus)
  const setAccount = useMT5Store((s) => s.setAccount)

  const health = useQuery({
    queryKey: ['health'],
    queryFn: () => getJson<HealthStatus>('/api/health'),
    refetchInterval: POLL_MS,
    retry: 1,
  })

  const account = useQuery({
    queryKey: ['account'],
    queryFn: () => getJson<Account & { bridge_live?: boolean }>('/api/account'),
    refetchInterval: POLL_MS,
    retry: 1,
  })

  useEffect(() => {
    const bridge = health.data?.bridge_detail
    const connected =
      health.isError || account.isError
        ? false
        : Boolean(bridge?.terminal_connected ?? account.data?.bridge_live)

    setStatus({
      connected,
      build: bridge?.terminal_build ?? null,
    })
  }, [health.data, health.isError, account.data, account.isError, setStatus])

  useEffect(() => {
    if (account.data && account.data.balance !== null) {
      const { login, balance, equity, margin, free_margin, margin_level_pct, currency } =
        account.data
      setAccount({
        login,
        balance,
        equity,
        margin,
        free_margin,
        margin_level_pct,
        currency,
      })
    } else if (account.isError) {
      setAccount(null)
    }
  }, [account.data, account.isError, setAccount])

  return { health, account }
}
