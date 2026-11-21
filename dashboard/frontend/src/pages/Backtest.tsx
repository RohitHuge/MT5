import { useMemo, useState } from 'react'
import * as TooltipPrimitive from '@radix-ui/react-tooltip'
import { useQuery } from '@tanstack/react-query'
import { clsx } from 'clsx'
import { FlaskConical, Play } from 'lucide-react'
import { useStrategies } from '../hooks/useStrategies'
import { asArray, getJson } from '../lib/api'
import { errorMessage } from '../lib/errors'
import { count as fmtCount, formatDate, ratio, signedMoney, toNumber } from '../lib/format'
import { EmptyState, LoadingState, Section } from '../components/StateViews'
import type { BacktestRun } from '../types/api'

const TIMEFRAMES = ['M1', 'M5', 'M15', 'H1'] as const
type Timeframe = (typeof TIMEFRAMES)[number]

const PHASE3_TOOLTIP = 'Backtest execution lands in Phase 3.'

function todayIso(): string {
  return new Date().toISOString().slice(0, 10)
}

function monthAgoIso(): string {
  const date = new Date()
  date.setMonth(date.getMonth() - 1)
  return date.toISOString().slice(0, 10)
}

const inputClass =
  'w-full rounded-md border border-border bg-bg px-2.5 py-1.5 text-xs text-slate-200 outline-none transition-colors focus:border-accent/60'

const labelClass = 'text-[11px] font-medium tracking-wide text-muted uppercase'

/** `GET /api/backtest/history` — added in Phase 2-Backend; may 404 today. */
function useBacktestHistory() {
  return useQuery({
    queryKey: ['backtest-history'],
    queryFn: async () => {
      const payload = await getJson<unknown>('/api/backtest/history')
      return asArray<BacktestRun>(payload)
    },
    // The endpoint may not exist yet: never retry hard, never show an error wall.
    retry: false,
    staleTime: 30_000,
  })
}

function HistoryTable({ rows }: { rows: BacktestRun[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] border-collapse text-xs">
        <thead>
          <tr className="border-b border-border text-left text-[10px] tracking-wider text-muted uppercase">
            <th className="py-2 pr-3 font-medium">Strategy</th>
            <th className="py-2 pr-3 font-medium">Symbol</th>
            <th className="py-2 pr-3 font-medium">TF</th>
            <th className="py-2 pr-3 font-medium">Range</th>
            <th className="py-2 pr-3 text-right font-medium">Trades</th>
            <th className="py-2 pr-3 text-right font-medium">Win rate</th>
            <th className="py-2 pr-3 text-right font-medium">Net P&L</th>
            <th className="py-2 pr-3 text-right font-medium">Max DD</th>
            <th className="py-2 pr-3 text-right font-medium">PF</th>
            <th className="py-2 text-right font-medium">Run</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const net = toNumber(row.net_pnl)
            return (
              <tr key={row.id} className="border-b border-border/60 last:border-0">
                <td className="py-2 pr-3 text-slate-200">{row.strategy}</td>
                <td className="py-2 pr-3 text-muted">{row.symbol ?? '—'}</td>
                <td className="py-2 pr-3 text-muted">{row.timeframe ?? '—'}</td>
                <td className="py-2 pr-3 text-muted">
                  {formatDate(row.from_date)} → {formatDate(row.to_date)}
                </td>
                <td className="py-2 pr-3 text-right text-slate-200 tabular-nums">
                  {fmtCount(toNumber(row.total_trades))}
                </td>
                <td className="py-2 pr-3 text-right text-slate-200 tabular-nums">
                  {toNumber(row.win_rate) === null ? '—' : `${toNumber(row.win_rate)?.toFixed(1)}%`}
                </td>
                <td
                  className={clsx(
                    'py-2 pr-3 text-right tabular-nums',
                    net === null ? 'text-muted' : net > 0 ? 'text-pos' : net < 0 ? 'text-neg' : 'text-slate-200',
                  )}
                >
                  {signedMoney(net)}
                </td>
                <td className="py-2 pr-3 text-right text-muted tabular-nums">
                  {toNumber(row.max_drawdown) === null ? '—' : signedMoney(-Math.abs(toNumber(row.max_drawdown) ?? 0))}
                </td>
                <td className="py-2 pr-3 text-right text-muted tabular-nums">
                  {ratio(toNumber(row.profit_factor))}
                </td>
                <td className="py-2 text-right text-muted">{formatDate(row.run_at)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export function Backtest() {
  const strategies = useStrategies()
  const history = useBacktestHistory()

  const [strategy, setStrategy] = useState('')
  const [symbol, setSymbol] = useState('XAUUSD')
  const [timeframe, setTimeframe] = useState<Timeframe>('M5')
  const [fromDate, setFromDate] = useState(monthAgoIso)
  const [toDate, setToDate] = useState(todayIso)
  const [tab, setTab] = useState<'config' | 'history'>('history') // TEMP-VERIFY

  const selected = useMemo(() => {
    const list = strategies.data ?? []
    if (list.length === 0) return ''
    return list.some((entry) => entry.name === strategy) ? strategy : list[0].name
  }, [strategies.data, strategy])

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-lg font-semibold">Backtesting</h1>
          <p className="mt-0.5 text-xs text-muted">
            Configure a run over historical candles. Execution arrives in Phase 3.
          </p>
        </div>
        <nav className="ml-auto flex gap-1 border-b border-border">
          {(['config', 'history'] as const).map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => setTab(key)}
              aria-pressed={tab === key}
              className={clsx(
                '-mb-px border-b-2 px-3 py-2 text-xs font-medium capitalize transition-colors',
                tab === key
                  ? 'border-accent text-accent'
                  : 'border-transparent text-muted hover:text-slate-200',
              )}
            >
              {key === 'config' ? 'Configure' : 'History'}
            </button>
          ))}
        </nav>
      </header>

      {tab === 'config' && (
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
          <aside className="w-full shrink-0 rounded-md border border-border bg-surface p-4 lg:w-[300px]">
            <h2 className="text-sm font-semibold text-slate-100">Run configuration</h2>

            <div className="mt-4 flex flex-col gap-3">
              <label className="flex flex-col gap-1">
                <span className={labelClass}>Strategy</span>
                <select
                  value={selected}
                  onChange={(event) => setStrategy(event.target.value)}
                  disabled={strategies.isPending || (strategies.data?.length ?? 0) === 0}
                  className={inputClass}
                >
                  {strategies.isPending && <option value="">Loading…</option>}
                  {!strategies.isPending && (strategies.data?.length ?? 0) === 0 && (
                    <option value="">No strategies available</option>
                  )}
                  {(strategies.data ?? []).map((entry) => (
                    <option key={entry.name} value={entry.name}>
                      {entry.display_name || entry.name}
                    </option>
                  ))}
                </select>
              </label>

              <label className="flex flex-col gap-1">
                <span className={labelClass}>Symbol</span>
                <input
                  value={symbol}
                  onChange={(event) => setSymbol(event.target.value.toUpperCase())}
                  placeholder="XAUUSD"
                  className={inputClass}
                />
              </label>

              <div className="flex flex-col gap-1">
                <span className={labelClass}>Timeframe</span>
                <div className="flex gap-1 rounded-md border border-border bg-bg p-0.5">
                  {TIMEFRAMES.map((option) => (
                    <button
                      key={option}
                      type="button"
                      onClick={() => setTimeframe(option)}
                      aria-pressed={timeframe === option}
                      className={clsx(
                        'flex-1 rounded px-2 py-1 text-[11px] font-medium transition-colors',
                        timeframe === option
                          ? 'bg-accent/20 text-accent'
                          : 'text-muted hover:bg-white/5 hover:text-slate-200',
                      )}
                    >
                      {option}
                    </button>
                  ))}
                </div>
              </div>

              <label className="flex flex-col gap-1">
                <span className={labelClass}>From</span>
                <input
                  type="date"
                  value={fromDate}
                  onChange={(event) => setFromDate(event.target.value)}
                  className={inputClass}
                />
              </label>

              <label className="flex flex-col gap-1">
                <span className={labelClass}>To</span>
                <input
                  type="date"
                  value={toDate}
                  onChange={(event) => setToDate(event.target.value)}
                  className={inputClass}
                />
              </label>

              <TooltipPrimitive.Provider delayDuration={200}>
                <TooltipPrimitive.Root>
                  <TooltipPrimitive.Trigger asChild>
                    {/* A disabled button swallows pointer events, so the tooltip
                        anchors on this wrapper span instead. */}
                    <span tabIndex={0} className="mt-1 cursor-not-allowed">
                      <button
                        type="button"
                        disabled
                        className="inline-flex w-full cursor-not-allowed items-center justify-center gap-2 rounded-md bg-accent/30 px-3 py-2 text-xs font-semibold text-white/60"
                      >
                        <Play size={13} />
                        Run backtest
                      </button>
                    </span>
                  </TooltipPrimitive.Trigger>
                  <TooltipPrimitive.Portal>
                    <TooltipPrimitive.Content
                      sideOffset={6}
                      className="z-50 rounded-md border border-border bg-surface px-2.5 py-1.5 text-[11px] text-slate-200 shadow-lg"
                    >
                      {PHASE3_TOOLTIP}
                    </TooltipPrimitive.Content>
                  </TooltipPrimitive.Portal>
                </TooltipPrimitive.Root>
              </TooltipPrimitive.Provider>

              <p className="text-[11px] text-muted">
                {selected
                  ? `${selected} · ${symbol || 'XAUUSD'} · ${timeframe}`
                  : 'Pick a strategy to configure a run.'}
              </p>
            </div>
          </aside>

          <div className="flex min-h-[320px] flex-1 flex-col items-center justify-center rounded-md border border-dashed border-border bg-surface/60 p-8 text-center">
            <FlaskConical size={28} className="text-muted" />
            <p className="mt-3 text-sm text-slate-300">Results will appear here</p>
            <p className="mt-1 max-w-sm text-xs text-muted">
              The run pipeline (bulk candles → strategy service → metrics) is wired in Phase 3. This
              panel will hold the equity curve, trade log and metric tiles.
            </p>
          </div>
        </div>
      )}

      {tab === 'history' && (
        <Section title="Past runs" subtitle="Most recent backtests, newest first">
          {history.isPending && <LoadingState label="Loading backtest history…" rows={3} />}
          {history.isError && (
            <EmptyState
              title="No backtest history available"
              hint={`The history endpoint is not available yet (${errorMessage(history.error, 'endpoint missing')}). It ships with Phase 2-Backend; runs will list here once it responds.`}
            />
          )}
          {history.isSuccess && (history.data?.length ?? 0) === 0 && (
            <EmptyState
              title="No backtest runs yet"
              hint="Run a backtest once Phase 3 lands — every run is stored and listed here."
            />
          )}
          {history.isSuccess && (history.data?.length ?? 0) > 0 && (
            <HistoryTable rows={history.data ?? []} />
          )}
          {history.isError && (
            <p className="mt-2 text-[11px] text-muted">
              Nothing is broken in the UI: the request simply could not be served.
            </p>
          )}
        </Section>
      )}
    </div>
  )
}
