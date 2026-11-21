import { create } from 'zustand'
import type { Account } from '../types/api'

interface MT5State {
  connected: boolean
  build: number | null
  account: Account | null
  lastChecked: Date | null
  setStatus: (data: {
    connected: boolean
    build?: number | null
    account?: Account | null
  }) => void
  setAccount: (account: Account | null) => void
}

export const useMT5Store = create<MT5State>((set) => ({
  connected: false,
  build: null,
  account: null,
  lastChecked: null,
  setStatus: ({ connected, build, account }) =>
    set((prev) => ({
      connected,
      build: build ?? prev.build,
      account: account ?? prev.account,
      lastChecked: new Date(),
    })),
  setAccount: (account) => set({ account, lastChecked: new Date() }),
}))
