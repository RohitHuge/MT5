import { useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { clsx } from 'clsx'
import { postJson } from '../lib/api'
import { errorMessage } from '../lib/errors'
import { asStrategyMode } from '../lib/strategyMode'
import type { SetModeResponse, StrategyMode } from '../types/api'

const MODES: StrategyMode[] = ['off', 'shadow', 'live']

const MODE_LABEL: Record<StrategyMode, string> = {
  live: 'LIVE',
  shadow: 'SHADOW',
  off: 'OFF',
}

const LIVE_WARNING =
  'This will place real orders on account 26396767. Shadow mode will be disabled for all other strategies.'

interface ModeToggleProps {
  /** Strategy slug, e.g. `ema-scalp`. */
  name: string
  /** Current server-side mode. */
  mode: StrategyMode | string | null | undefined
  /** Optional display label used in the confirmation dialog. */
  displayName?: string
  className?: string
  /** Render a compact control (used inside dense tables). */
  compact?: boolean
}

/**
 * OFF → SHADOW → LIVE selector backed by `POST /api/strategy/:name/set_mode`.
 *
 * Promoting to LIVE always goes through a confirmation dialog: the backend
 * demotes every other strategy to shadow in the same transaction, so an
 * accidental click would hijack the live account.
 */
export function ModeToggle({ name, mode, displayName, className, compact }: ModeToggleProps) {
  const queryClient = useQueryClient()
  const current = asStrategyMode(mode)

  const [confirmLive, setConfirmLive] = useState(false) // TEMP-VERIFY-REVERTED
  const [error, setError] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: (next: StrategyMode) =>
      postJson<SetModeResponse, { mode: StrategyMode }>(
        `/api/strategy/${encodeURIComponent(name)}/set_mode`,
        { mode: next },
      ),
    onSuccess: async () => {
      setError(null)
      setConfirmLive(false)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['strategies'] }),
        queryClient.invalidateQueries({ queryKey: ['compare'] }),
        queryClient.invalidateQueries({ queryKey: ['live-positions'] }),
        queryClient.invalidateQueries({ queryKey: ['strategy-perf'] }),
        queryClient.invalidateQueries({ queryKey: ['equity-curve'] }),
      ])
    },
    onError: (err) => {
      setError(errorMessage(err, 'Could not change strategy mode'))
    },
  })

  const pending = mutation.isPending

  function request(next: StrategyMode) {
    if (next === current || pending) return
    setError(null)
    if (next === 'live') {
      setConfirmLive(true)
      return
    }
    mutation.mutate(next)
  }

  return (
    <div className={clsx('flex flex-col gap-1', className)}>
      <div className="inline-flex items-center gap-1 rounded-md border border-border bg-bg p-0.5">
        {MODES.map((option) => {
          const active = option === current
          const busy = pending && mutation.variables === option
          return (
            <button
              key={option}
              type="button"
              disabled={pending || active}
              onClick={() => request(option)}
              aria-pressed={active}
              title={
                option === 'live'
                  ? 'Promote to LIVE (requires confirmation)'
                  : `Switch to ${MODE_LABEL[option]}`
              }
              className={clsx(
                'inline-flex items-center gap-1 rounded px-2 font-semibold tracking-wide uppercase transition-colors',
                compact ? 'py-0.5 text-[10px]' : 'py-1 text-[11px]',
                active
                  ? option === 'live'
                    ? 'bg-live/20 text-live'
                    : option === 'shadow'
                      ? 'bg-shadow/20 text-shadow'
                      : 'bg-off/25 text-slate-200'
                  : 'text-muted hover:bg-white/5 hover:text-slate-200',
                pending && !busy && 'opacity-50',
                pending && 'cursor-not-allowed',
              )}
            >
              {busy && <Loader2 size={10} className="animate-spin" />}
              {MODE_LABEL[option]}
            </button>
          )
        })}
      </div>

      {error && <p className="text-[11px] text-neg">{error}</p>}

      <Dialog.Root open={confirmLive} onOpenChange={(open) => !pending && setConfirmLive(open)}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-40 bg-black/70" />
          <Dialog.Content className="fixed top-1/2 left-1/2 z-50 w-[min(28rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-md border border-border bg-surface p-5 shadow-xl">
            <Dialog.Title className="text-sm font-semibold text-slate-100">
              Promote {displayName ?? name} to LIVE?
            </Dialog.Title>
            <Dialog.Description className="mt-3 rounded-md border border-neg/40 bg-neg/10 px-3 py-2 text-xs text-slate-200">
              {LIVE_WARNING}
            </Dialog.Description>
            <div className="mt-5 flex justify-end gap-2">
              <Dialog.Close asChild>
                <button
                  type="button"
                  disabled={pending}
                  className="rounded-md border border-border px-3 py-1.5 text-xs text-muted transition-colors hover:text-slate-200 disabled:opacity-50"
                >
                  Cancel
                </button>
              </Dialog.Close>
              <button
                type="button"
                disabled={pending}
                onClick={() => mutation.mutate('live')}
                className="inline-flex items-center gap-1.5 rounded-md bg-neg px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-neg/85 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {pending && <Loader2 size={12} className="animate-spin" />}
                {pending ? 'Promoting…' : 'Promote to LIVE'}
              </button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  )
}
