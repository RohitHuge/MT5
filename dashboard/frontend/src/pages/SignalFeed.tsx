import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowDown, Loader2, RefreshCw, Trash2, WifiOff } from 'lucide-react'
import { clsx } from 'clsx'
import { useSignalFeed } from '../hooks/useSignalFeed'
import { EmptyState, Section } from '../components/StateViews'
import { formatDateTime, formatTime } from '../lib/format'
import type { SignalEvent } from '../types/api'

type FilterKey = 'all' | 'ema' | 'obfvg' | 'liq'

interface FilterDef {
  key: FilterKey
  label: string
  /** Lowercase substrings matched against the raw strategy name. */
  matches: string[]
}

/**
 * Strategy names come from the API / socket, so the filter matches on
 * substrings of the slug rather than hardcoding a closed list.
 */
const FILTERS: FilterDef[] = [
  { key: 'all', label: 'ALL', matches: [] },
  { key: 'ema', label: 'EMA', matches: ['ema'] },
  { key: 'obfvg', label: 'OB+FVG', matches: ['ob-fvg', 'ob_fvg', 'obfvg', 'ob+fvg', 'order-block'] },
  { key: 'liq', label: 'LIQ', matches: ['liq', 'sweep'] },
]

function matchFilter(strategy: string, filter: FilterKey): boolean {
  if (filter === 'all') return true
  const definition = FILTERS.find((entry) => entry.key === filter)
  if (!definition) return true
  const haystack = strategy.toLowerCase()
  return definition.matches.some((needle) => haystack.includes(needle))
}

function ConnectionDot({ status }: { status: 'connecting' | 'connected' | 'disconnected' }) {
  const dot =
    status === 'connected' ? 'bg-live shadow-[0_0_6px_#22c55e]' : status === 'connecting' ? 'bg-accent' : 'bg-neg'
  const label =
    status === 'connected' ? 'Connected' : status === 'connecting' ? 'Connecting…' : 'Disconnected'
  const tone = status === 'connected' ? 'text-live' : status === 'connecting' ? 'text-accent' : 'text-neg'

  return (
    <div className="flex items-center gap-2 text-xs">
      {status === 'connecting' ? (
        <Loader2 size={12} className="animate-spin text-accent" />
      ) : (
        <span className={clsx('h-2 w-2 rounded-full', dot)} />
      )}
      <span className={tone}>{label}</span>
    </div>
  )
}

function DirectionBadge({ direction }: { direction: SignalEvent['direction'] }) {
  const tone =
    direction === 'BUY'
      ? 'bg-pos/15 text-pos border-pos/40'
      : direction === 'SELL'
        ? 'bg-neg/15 text-neg border-neg/40'
        : 'bg-off/15 text-off border-off/40'
  return (
    <span
      className={clsx(
        'inline-flex items-center rounded-md border px-2 py-0.5 text-[10px] font-bold tracking-wider uppercase',
        tone,
      )}
    >
      {direction}
    </span>
  )
}

function PriceCell({ label, value }: { label: string; value: number | null }) {
  return (
    <div>
      <p className="text-[10px] tracking-wide text-muted uppercase">{label}</p>
      <p className="text-xs text-slate-200 tabular-nums">{value === null ? '—' : value.toFixed(2)}</p>
    </div>
  )
}

function SignalCard({ signal }: { signal: SignalEvent }) {
  const confidence = signal.confidence
  return (
    <article className="rounded-md border border-border bg-surface p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded bg-accent/15 px-1.5 py-0.5 text-[10px] font-semibold text-accent">
          {signal.strategy}
        </span>
        <DirectionBadge direction={signal.direction} />
        <span className="text-xs font-medium text-slate-200">{signal.symbol}</span>
        {signal.mode && (
          <span className="rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-muted uppercase">
            {signal.mode}
          </span>
        )}
        <span className="ml-auto text-[11px] text-muted" title={formatDateTime(signal.timestamp ?? signal.receivedAt)}>
          {signal.timestamp ? formatTime(signal.timestamp) : formatTime(signal.receivedAt)}
        </span>
      </div>

      <div className="mt-3 grid grid-cols-3 gap-2 sm:grid-cols-5">
        <PriceCell label="Entry" value={signal.entryPrice} />
        <PriceCell label="SL" value={signal.sl} />
        <PriceCell label="TP" value={signal.tp} />
        <PriceCell label="Lots" value={signal.lots} />
        {confidence !== null && (
          <div>
            <p className="text-[10px] tracking-wide text-muted uppercase">Conf.</p>
            <p className="text-xs text-slate-200 tabular-nums">{(confidence * 100).toFixed(0)}%</p>
          </div>
        )}
      </div>

      {signal.comment && <p className="mt-2 text-[11px] text-muted">{signal.comment}</p>}
    </article>
  )
}

export function SignalFeed() {
  const [filter, setFilter] = useState<FilterKey>('all')
  const [paused, setPaused] = useState(false)

  const { signals, status, pendingCount, clear, reconnect } = useSignalFeed({ paused })

  const scrollRef = useRef<HTMLDivElement | null>(null)

  /** Re-enable auto-scroll and jump back to the newest signal. */
  const resume = useCallback(() => {
    setPaused(false)
    if (scrollRef.current) scrollRef.current.scrollTop = 0
  }, [])

  const handleScroll = useCallback((event: React.UIEvent<HTMLDivElement>) => {
    const target = event.currentTarget
    // Feed is newest-first, so scrolling *up* means moving away from the top.
    setPaused(target.scrollTop > 24)
  }, [])

  const filtered = useMemo(
    () => signals.filter((signal) => matchFilter(signal.strategy, filter)),
    [signals, filter],
  )

  // Keep the view pinned to the newest card while live.
  useEffect(() => {
    if (!paused && scrollRef.current) scrollRef.current.scrollTop = 0
  }, [filtered.length, paused])

  const emptyStream = signals.length === 0

  return (
    <div className="flex h-[calc(100vh-6.5rem)] flex-col gap-4">
      <header className="flex flex-wrap items-center gap-3">
        <div>
          <h1 className="text-lg font-semibold">Signal Feed</h1>
          <p className="mt-0.5 text-xs text-muted">
            Live signals from every strategy · last 50 kept, newest first
          </p>
        </div>
        <div className="ml-auto flex items-center gap-3">
          <ConnectionDot status={status} />
          {status === 'disconnected' && (
            <button
              type="button"
              onClick={reconnect}
              className="inline-flex items-center gap-1 rounded-md border border-border bg-surface px-2.5 py-1 text-[11px] text-muted transition-colors hover:text-slate-200"
            >
              <RefreshCw size={11} />
              Reconnect
            </button>
          )}
          <button
            type="button"
            onClick={clear}
            disabled={emptyStream}
            className="inline-flex items-center gap-1 rounded-md border border-border bg-surface px-2.5 py-1 text-[11px] text-muted transition-colors hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Trash2 size={11} />
            Clear
          </button>
        </div>
      </header>

      <div className="flex flex-wrap items-center gap-1 rounded-md border border-border bg-bg p-0.5">
        {FILTERS.map((definition) => {
          const active = definition.key === filter
          return (
            <button
              key={definition.key}
              type="button"
              onClick={() => setFilter(definition.key)}
              aria-pressed={active}
              className={clsx(
                'rounded px-2.5 py-1 text-[11px] font-medium transition-colors',
                active ? 'bg-accent/20 text-accent' : 'text-muted hover:bg-white/5 hover:text-slate-200',
              )}
            >
              {definition.label}
            </button>
          )
        })}
        <span className="ml-auto px-2 text-[11px] text-muted">
          {filtered.length} signal{filtered.length === 1 ? '' : 's'}
          {filter !== 'all' ? ` of ${signals.length}` : ''}
        </span>
      </div>

      <div className="relative min-h-0 flex-1">
        <div
          ref={scrollRef}
          onScroll={handleScroll}
          className="h-full overflow-y-auto rounded-md border border-border bg-surface/40 p-3"
        >
          {filtered.length === 0 && (
            <EmptyState
              title={emptyStream ? 'Waiting for signals…' : 'No signals match this filter'}
              hint={
                emptyStream
                  ? status === 'connected'
                    ? 'Connected to the signal bus. Signals appear here the moment a strategy fires.'
                    : status === 'connecting'
                      ? 'Connecting to the signal bus…'
                      : 'Not connected to the signal bus — retrying with backoff.'
                  : 'Clear the filter or wait for the next signal from this strategy.'
              }
            />
          )}

          {filtered.length > 0 && (
            <div className="flex max-h-full flex-col gap-2">
              {filtered.map((signal) => (
                <SignalCard key={signal.id} signal={signal} />
              ))}
            </div>
          )}
        </div>

        {paused && (
          <button
            type="button"
            onClick={resume}
            className="absolute bottom-4 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-accent/40 bg-surface px-3 py-1.5 text-xs text-accent shadow-lg transition-colors hover:bg-accent/15"
          >
            <ArrowDown size={12} />
            {pendingCount > 0 ? `Resume · ${pendingCount} new` : 'Resume live feed'}
          </button>
        )}
      </div>

      {status === 'disconnected' && (
        <p className="flex items-center gap-1.5 text-[11px] text-neg">
          <WifiOff size={12} /> Connection lost — reconnecting with exponential backoff (max 30s).
        </p>
      )}

      <Section title="How this works" subtitle="Feed behaviour" className="shrink-0">
        <p className="text-[11px] text-muted">
          The feed auto-pauses when you scroll up so cards do not shift while you read. Signals keep
          arriving in the background and are merged back in when you resume. Keepalive pings are ignored.
        </p>
      </Section>
    </div>
  )
}
