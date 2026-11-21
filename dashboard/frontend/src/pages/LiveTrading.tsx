import { useCallback, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import * as TooltipPrimitive from '@radix-ui/react-tooltip'
import { ChevronDown, ChevronRight, Loader2, Send, X } from 'lucide-react'
import { clsx } from 'clsx'
import { CandleChart } from '../components/charts/CandleChart'
import { StrategyModeBadge } from '../components/StrategyModeBadge'
import { ModeToggle } from '../components/ModeToggle'
import { EmptyState, ErrorState, LoadingState, Section } from '../components/StateViews'
import { livePositionsQueryKey, useLivePositions } from '../hooks/useLivePositions'
import { useStrategies } from '../hooks/useStrategies'
import { errorMessage } from '../lib/errors'
import { money, pnlTone, signedMoney, toNumber } from '../lib/format'
import { postJson } from '../lib/api'
import { useMT5Store } from '../store/mt5Store'
import type { RealTrade, VirtualTrade } from '../types/api'

const TIMEFRAMES = ['M1', 'M5', 'M15', 'H1'] as const
type Timeframe = (typeof TIMEFRAMES)[number]

const inputClass =
  'w-full rounded-md border border-border bg-bg px-2.5 py-1.5 text-xs text-slate-200 outline-none transition-colors focus:border-accent/60'
const labelClass = 'text-[11px] font-medium tracking-wide text-muted uppercase'

function price(value: number | null | undefined): string {
  const n = toNumber(value)
  return n === null ? '—' : n.toFixed(2)
}

function pnlCell(value: number | null | undefined): string {
  return signedMoney(toNumber(value))
}

function StrategyModeCard({
  name,
  displayName,
  mode,
}: {
  name: string
  displayName: string
  mode: string
}) {
  return (
    <div className="flex items-center gap-3 rounded-md border border-border bg-bg px-3 py-2.5">
      <div className="min-w-0">
        <p className="truncate text-xs font-medium text-slate-100">{displayName}</p>
        <p className="text-[11px] text-muted">{name}</p>
      </div>
      <StrategyModeBadge mode={mode} className="ml-auto shrink-0" />
      <ModeToggle name={name} mode={mode} displayName={displayName} />
    </div>
  )
}

function RealPositionsTable() {
  const queryClient = useQueryClient()
  const positions = useLivePositions()
  const [closing, setClosing] = useState<number | null>(null)

  const closePosition = useCallback(
    async (ticket: number) => {
      setClosing(ticket)
      try {
        await postJson(`/api/close/${ticket}`, {})
        await queryClient.invalidateQueries({ queryKey: livePositionsQueryKey })
      } catch {
        // The row stays put; the poll will reconcile it.
      } finally {
        setClosing(null)
      }
    },
    [queryClient],
  )

  if (positions.isPending) return <LoadingState label="Loading open positions…" rows={2} />
  if (positions.isError) {
    return (
      <ErrorState
        message={errorMessage(positions.error, 'Could not load open positions')}
        onRetry={() => void positions.refetch()}
      />
    )
  }

  const rows: RealTrade[] = positions.data?.real ?? []
  if (rows.length === 0) {
    return <EmptyState title="No open real trades" hint="The executor opens a position when a live strategy signals." />
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] border-collapse text-xs">
        <thead>
          <tr className="border-b border-border text-left text-[10px] tracking-wider text-muted uppercase">
            <th className="py-2 pr-3 font-medium">Ticket</th>
            <th className="py-2 pr-3 font-medium">Symbol</th>
            <th className="py-2 pr-3 font-medium">Dir</th>
            <th className="py-2 pr-3 text-right font-medium">Lots</th>
            <th className="py-2 pr-3 text-right font-medium">Entry</th>
            <th className="py-2 pr-3 text-right font-medium">P&amp;L</th>
            <th className="py-2 pr-3 text-right font-medium">SL</th>
            <th className="py-2 pr-3 text-right font-medium">TP</th>
            <th className="py-2 pr-3 text-right font-medium">Action</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((trade) => {
            const pnl = toNumber(trade.unrealised_pnl)
            return (
              <tr key={trade.ticket} className="border-b border-border/60 last:border-0">
                <td className="py-2 pr-3 tabular-nums text-muted">{trade.ticket}</td>
                <td className="py-2 pr-3">{trade.symbol}</td>
                <td
                  className={clsx(
                    'py-2 pr-3 font-medium',
                    trade.direction === 'BUY' ? 'text-pos' : 'text-neg',
                  )}
                >
                  {trade.direction}
                </td>
                <td className="py-2 pr-3 text-right tabular-nums">{trade.lots ?? '—'}</td>
                <td className="py-2 pr-3 text-right tabular-nums">{price(trade.open_price)}</td>
                <td className={clsx('py-2 pr-3 text-right tabular-nums', pnlTone(pnl))}>
                  {pnlCell(pnl)}
                </td>
                <td className="py-2 pr-3 text-right tabular-nums text-muted">{price(trade.sl_price)}</td>
                <td className="py-2 pr-3 text-right tabular-nums text-muted">{price(trade.tp_price)}</td>
                <td className="py-2 pr-3 text-right">
                  <button
                    type="button"
                    onClick={() => void closePosition(trade.ticket)}
                    disabled={closing === trade.ticket}
                    className="inline-flex items-center gap-1 rounded border border-neg/40 px-2 py-0.5 text-[11px] font-medium text-neg transition-colors hover:bg-neg/10 disabled:opacity-50"
                  >
                    {closing === trade.ticket ? <Loader2 size={11} className="animate-spin" /> : <X size={11} />}
                    Close
                  </button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function ShadowPositionsTable() {
  const positions = useLivePositions()

  if (positions.isPending) return <LoadingState label="Loading shadow positions…" rows={2} />
  if (positions.isError) {
    return (
      <ErrorState
        message={errorMessage(positions.error, 'Could not load shadow positions')}
        onRetry={() => void positions.refetch()}
      />
    )
  }

  const rows: VirtualTrade[] = positions.data?.shadow ?? []
  if (rows.length === 0) {
    return <EmptyState title="No open shadow trades" hint="Every signal is recorded as a virtual trade, even when no strategy is live." />
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[680px] border-collapse text-xs">
        <thead>
          <tr className="border-b border-border text-left text-[10px] tracking-wider text-muted uppercase">
            <th className="py-2 pr-3 font-medium">Strategy</th>
            <th className="py-2 pr-3 font-medium">Symbol</th>
            <th className="py-2 pr-3 font-medium">Dir</th>
            <th className="py-2 pr-3 text-right font-medium">Lots</th>
            <th className="py-2 pr-3 text-right font-medium">Entry</th>
            <th className="py-2 pr-3 text-right font-medium">Current</th>
            <th className="py-2 pr-3 text-right font-medium">SL</th>
            <th className="py-2 pr-3 text-right font-medium">TP</th>
            <th className="py-2 pr-3 text-right font-medium">P&amp;L</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((trade) => {
            const pnl = toNumber(trade.unrealised_pnl)
            return (
              <tr key={trade.id} className="border-b border-border/60 last:border-0">
                <td className="py-2 pr-3">{trade.strategy}</td>
                <td className="py-2 pr-3">{trade.symbol}</td>
                <td
                  className={clsx(
                    'py-2 pr-3 font-medium',
                    trade.direction === 'BUY' ? 'text-pos' : 'text-neg',
                  )}
                >
                  {trade.direction}
                </td>
                <td className="py-2 pr-3 text-right tabular-nums">{trade.lots ?? '—'}</td>
                <td className="py-2 pr-3 text-right tabular-nums">{price(trade.entry_price)}</td>
                <td className="py-2 pr-3 text-right tabular-nums">{price(trade.current_price)}</td>
                <td className="py-2 pr-3 text-right tabular-nums text-muted">{price(trade.sl_price)}</td>
                <td className="py-2 pr-3 text-right tabular-nums text-muted">{price(trade.tp_price)}</td>
                <td className={clsx('py-2 pr-3 text-right tabular-nums', pnlTone(pnl))}>
                  {pnlCell(pnl)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function ManualOrderPanel() {
  const connected = useMT5Store((s) => s.connected)
  const account = useMT5Store((s) => s.account)
  const [open, setOpen] = useState(false)
  const [direction, setDirection] = useState<'BUY' | 'SELL'>('BUY')
  const [lots, setLots] = useState('0.01')
  const [sl, setSl] = useState('')
  const [tp, setTp] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [feedback, setFeedback] = useState<string | null>(null)

  const submit = async () => {
    const lotsNum = Number(lots)
    const slNum = Number(sl)
    const tpNum = Number(tp)
    if (!Number.isFinite(lotsNum) || lotsNum <= 0) {
      setFeedback('Lots must be greater than 0.')
      return
    }
    if (!Number.isFinite(slNum) || !Number.isFinite(tpNum)) {
      setFeedback('SL and TP must be valid prices.')
      return
    }

    setSubmitting(true)
    setFeedback(null)
    try {
      await postJson('/api/order', {
        symbol: 'XAUUSD',
        direction,
        lots: lotsNum,
        sl: slNum,
        tp: tpNum,
        comment: 'manual',
      })
      setFeedback('Order submitted.')
      setSl('')
      setTp('')
    } catch (err) {
      setFeedback(errorMessage(err, 'Order rejected'))
    } finally {
      setSubmitting(false)
    }
  }

  const disabled = !connected

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 rounded-md border border-border bg-bg px-3 py-2 text-xs font-medium text-slate-200 transition-colors hover:border-accent/50"
        aria-pressed={open}
      >
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        Manual order
        <span className="ml-auto text-[11px] text-muted">
          {connected ? 'Bridge connected' : 'Bridge offline'}
        </span>
      </button>

      {open && (
        <div className="mt-3 flex flex-col gap-3">
          <div className="flex gap-1 rounded-md border border-border bg-bg p-0.5">
            {(['BUY', 'SELL'] as const).map((side) => (
              <button
                key={side}
                type="button"
                onClick={() => setDirection(side)}
                aria-pressed={direction === side}
                className={clsx(
                  'flex-1 rounded px-2 py-1 text-[11px] font-medium transition-colors',
                  direction === side
                    ? side === 'BUY'
                      ? 'bg-pos/20 text-pos'
                      : 'bg-neg/20 text-neg'
                    : 'text-muted hover:bg-white/5 hover:text-slate-200',
                )}
              >
                {side}
              </button>
            ))}
          </div>

          <div className="grid grid-cols-3 gap-2">
            <label className="flex flex-col gap-1">
              <span className={labelClass}>Lots</span>
              <input
                type="number"
                step="0.01"
                min="0.01"
                value={lots}
                onChange={(e) => setLots(e.target.value)}
                className={inputClass}
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className={labelClass}>SL</span>
              <input
                type="number"
                step="0.01"
                value={sl}
                onChange={(e) => setSl(e.target.value)}
                placeholder="price"
                className={inputClass}
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className={labelClass}>TP</span>
              <input
                type="number"
                step="0.01"
                value={tp}
                onChange={(e) => setTp(e.target.value)}
                placeholder="price"
                className={inputClass}
              />
            </label>
          </div>

          {disabled ? (
            <TooltipPrimitive.Provider delayDuration={200}>
              <TooltipPrimitive.Root>
                <TooltipPrimitive.Trigger asChild>
                  {/* A disabled button swallows pointer events, so the tooltip
                      anchors on this wrapper span instead. */}
                  <span tabIndex={0} className="cursor-not-allowed">
                    <button
                      type="button"
                      disabled
                      className="inline-flex w-full cursor-not-allowed items-center justify-center gap-2 rounded-md bg-accent/30 px-3 py-2 text-xs font-semibold text-white/60"
                    >
                      <Send size={13} />
                      Submit order
                    </button>
                  </span>
                </TooltipPrimitive.Trigger>
                <TooltipPrimitive.Portal>
                  <TooltipPrimitive.Content
                    sideOffset={6}
                    className="z-50 rounded-md border border-border bg-surface px-2.5 py-1.5 text-[11px] text-slate-200 shadow-lg"
                  >
                    Bridge offline
                  </TooltipPrimitive.Content>
                </TooltipPrimitive.Portal>
              </TooltipPrimitive.Root>
            </TooltipPrimitive.Provider>
          ) : (
            <button
              type="button"
              onClick={() => void submit()}
              disabled={submitting}
              className="inline-flex w-full items-center justify-center gap-2 rounded-md bg-accent/80 px-3 py-2 text-xs font-semibold text-white transition-colors hover:bg-accent disabled:opacity-60"
            >
              {submitting ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} />}
              Submit order
            </button>
          )}

          {account && (
            <p className="text-[11px] text-muted">
              Real orders go to account {account.login} — {money(account.balance)}
            </p>
          )}
          {feedback && <p className="text-[11px] text-slate-300">{feedback}</p>}
        </div>
      )}
    </div>
  )
}

/** Live chart, bot control, open positions and manual order entry. */
export function LiveTrading() {
  const strategies = useStrategies()
  const [timeframe, setTimeframe] = useState<Timeframe>('M5')

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-lg font-semibold">Live Trading</h1>
          <p className="mt-0.5 text-xs text-muted">
            XAUUSD candles, strategy modes and open positions.
          </p>
        </div>
        <div className="ml-auto flex gap-1 rounded-md border border-border bg-bg p-0.5">
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf}
              type="button"
              onClick={() => setTimeframe(tf)}
              aria-pressed={timeframe === tf}
              className={clsx(
                'rounded px-2.5 py-1 text-[11px] font-medium transition-colors',
                timeframe === tf
                  ? 'bg-accent/20 text-accent'
                  : 'text-muted hover:bg-white/5 hover:text-slate-200',
              )}
            >
              {tf}
            </button>
          ))}
        </div>
      </header>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="rounded-md border border-border bg-surface p-2">
          <CandleChart symbol="XAUUSD" timeframe={timeframe} height={520} />
        </div>

        <div className="flex flex-col gap-4">
          <Section title="Bot control" subtitle="Strategy modes — promotion is confirmed first">
            {strategies.isPending && <LoadingState label="Loading strategies…" rows={2} />}
            {strategies.isError && (
              <ErrorState
                message={errorMessage(strategies.error, 'Could not load strategies')}
                onRetry={() => void strategies.refetch()}
              />
            )}
            {strategies.isSuccess && (strategies.data?.length ?? 0) === 0 && (
              <EmptyState
                title="No strategies configured"
                hint="Strategy services self-register on startup."
              />
            )}
            {strategies.isSuccess && (strategies.data?.length ?? 0) > 0 && (
              <div className="flex flex-col gap-2">
                {(strategies.data ?? []).map((strategy) => (
                  <StrategyModeCard
                    key={strategy.name}
                    name={strategy.name}
                    displayName={strategy.display_name}
                    mode={strategy.mode}
                  />
                ))}
              </div>
            )}
          </Section>

          <Section title="Open positions" subtitle="Real trades — polls every 10s">
            <RealPositionsTable />
          </Section>

          <Section title="Shadow / Virtual" subtitle="Recorded for every signal, live or not">
            <ShadowPositionsTable />
          </Section>

          <Section title="Manual order" subtitle="Direct order entry to MT5">
            <ManualOrderPanel />
          </Section>
        </div>
      </div>
    </div>
  )
}
