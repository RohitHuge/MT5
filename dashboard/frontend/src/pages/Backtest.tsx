import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { clsx } from 'clsx'
import { Download, FlaskConical, Loader2, Play } from 'lucide-react'
import { EquityCurve, type EquityPoint } from '../components/charts/EquityCurve'
import { MetricsRow, type Metric } from '../components/charts/MetricsRow'
import { EmptyState, ErrorState, LoadingState, Section } from '../components/StateViews'
import { useStrategies } from '../hooks/useStrategies'
import { asArray, getJson, postJson } from '../lib/api'
import { errorMessage } from '../lib/errors'
import {
  count as fmtCount,
  formatDate,
  formatDateTime,
  pctFromRatio,
  ratio,
  signedMoney,
  toNumber,
} from '../lib/format'
import type {
  BacktestRequest,
  BacktestResult,
  BacktestRun,
  BacktestTrade,
} from '../types/api'

const TIMEFRAMES = ['M1', 'M5', 'M15', 'H1'] as const
type Timeframe = (typeof TIMEFRAMES)[number]

/** Per-strategy parameter overrides rendered in the config panel. */
const PARAM_FIELDS: Record<string, { key: string; label: string; step: string }[]> = {
  'ema-scalp': [
    { key: 'risk_pct', label: 'Risk %', step: '0.001' },
    { key: 'rr_target', label: 'RR target', step: '0.1' },
    { key: 'rsi_min_buy', label: 'RSI min buy', step: '1' },
    { key: 'rsi_max_sell', label: 'RSI max sell', step: '1' },
  ],
  'ob-fvg': [
    { key: 'risk_pct', label: 'Risk %', step: '0.001' },
    { key: 'rr_target', label: 'RR target', step: '0.1' },
    { key: 'min_impulse_points', label: 'Min impulse pts', step: '1' },
    { key: 'min_gap_points', label: 'Min gap pts', step: '1' },
  ],
  'liq-sweep': [
    { key: 'risk_pct', label: 'Risk %', step: '0.001' },
    { key: 'rr_target', label: 'RR target', step: '0.1' },
    { key: 'pivot_left', label: 'Pivot left', step: '1' },
    { key: 'pivot_right', label: 'Pivot right', step: '1' },
  ],
}

const inputClass =
  'w-full rounded-md border border-border bg-bg px-2.5 py-1.5 text-xs text-slate-200 outline-none transition-colors focus:border-accent/60'

const labelClass = 'text-[11px] font-medium tracking-wide text-muted uppercase'

function todayIso(): string {
  return new Date().toISOString().slice(0, 10)
}

function monthAgoIso(): string {
  const date = new Date()
  date.setMonth(date.getMonth() - 1)
  return date.toISOString().slice(0, 10)
}

/** `GET /api/backtest/history` */
function useBacktestHistory() {
  return useQuery({
    queryKey: ['backtest-history'],
    queryFn: async () => {
      const payload = await getJson<unknown>('/api/backtest/history')
      return asArray<BacktestRun>(payload)
    },
    retry: false,
    staleTime: 30_000,
  })
}

function csvEscape(value: unknown): string {
  const text = value === null || value === undefined ? '' : String(value)
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

function tradesToCsv(trades: ReadonlyArray<BacktestTrade>): string {
  const headers = [
    'direction', 'entry_price', 'sl_price', 'tp_price',
    'close_price', 'outcome', 'lots', 'gross_pnl', 'commission', 'net_pnl',
    'opened_at', 'closed_at',
  ]
  const rows = trades.map((t) =>
    [
      t.direction, t.entry_price, t.sl_price, t.tp_price,
      t.close_price, t.outcome, t.lots, t.gross_pnl, t.commission, t.net_pnl,
      t.opened_at, t.closed_at,
    ]
      .map(csvEscape)
      .join(','),
  )
  return [headers.join(','), ...rows].join('\n')
}

/** Running P&L for the equity chart — the backend returns no cumulative series. */
function cumulativeCurve(trades: ReadonlyArray<BacktestTrade>): EquityPoint[] {
  let running = 0
  return trades.map((trade, index) => {
    running += toNumber(trade.net_pnl) ?? 0
    return {
      date: trade.closed_at ?? trade.opened_at ?? `#${index + 1}`,
      cumulative_pnl: Math.round(running * 100) / 100,
    }
  })
}

function resultMetrics(result: BacktestResult): Metric[] {
  const m = result.metrics
  const net = m?.net_pnl ?? toNumber(result.net_pnl) ?? 0
  const drawdown = m?.max_drawdown ?? toNumber(result.max_drawdown) ?? 0
  return [
    { label: 'Total trades', value: fmtCount(m?.total_trades ?? toNumber(result.total_trades)) },
    { label: 'Win rate', value: pctFromRatio(m?.win_rate ?? toNumber(result.win_rate)) },
    { label: 'Net P&L', value: signedMoney(net), color: net >= 0 ? 'text-pos' : 'text-neg' },
    {
      label: 'Max drawdown',
      value: signedMoney(-Math.abs(drawdown)),
      color: drawdown > 0 ? 'text-neg' : undefined,
    },
    { label: 'Profit factor', value: ratio(m?.profit_factor ?? toNumber(result.profit_factor)) },
    { label: 'Sharpe', value: ratio(m?.sharpe ?? toNumber(result.sharpe)) },
  ]
}

function TradeLogTable({ trades }: { trades: ReadonlyArray<BacktestTrade> }) {
  // Precompute so the cumulative column does not rely on mutation during render.
  const rows = trades.map((trade, index) => {
    const pnl = toNumber(trade.net_pnl) ?? 0
    const cumulative =
      trades.slice(0, index + 1).reduce((sum, t) => sum + (toNumber(t.net_pnl) ?? 0), 0)
    return { trade, index, pnl, cumulative }
  })

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] border-collapse text-xs">
        <thead>
          <tr className="border-b border-border text-left text-[10px] tracking-wider text-muted uppercase">
            <th className="py-2 pr-3 font-medium">Date</th>
            <th className="py-2 pr-3 font-medium">Direction</th>
            <th className="py-2 pr-3 text-right font-medium">Entry</th>
            <th className="py-2 pr-3 text-right font-medium">Fill</th>
            <th className="py-2 pr-3 text-right font-medium">Close</th>
            <th className="py-2 pr-3 font-medium">Outcome</th>
            <th className="py-2 pr-3 text-right font-medium">P&amp;L</th>
            <th className="py-2 pr-3 text-right font-medium">Cumulative</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ trade, index, pnl, cumulative }) => (
            <tr
              key={`${trade.opened_at ?? index}-${index}`}
              className="border-b border-border/60 last:border-0"
            >
              <td className="py-2 pr-3 text-muted">{formatDateTime(trade.opened_at)}</td>
              <td
                className={clsx(
                  'py-2 pr-3 font-medium',
                  trade.direction === 'BUY' ? 'text-pos' : 'text-neg',
                )}
              >
                {trade.direction}
              </td>
              <td className="py-2 pr-3 text-right tabular-nums">
                {toNumber(trade.entry_price)?.toFixed(2) ?? '—'}
              </td>
              <td className="py-2 pr-3 text-right tabular-nums text-muted">
                {toNumber(trade.entry_price)?.toFixed(2) ?? '—'}
              </td>
              <td className="py-2 pr-3 text-right tabular-nums">
                {toNumber(trade.close_price)?.toFixed(2) ?? '—'}
              </td>
              <td
                className={clsx(
                  'py-2 pr-3 font-medium',
                  trade.outcome === 'TP'
                    ? 'text-pos'
                    : trade.outcome === 'SL'
                      ? 'text-neg'
                      : 'text-muted',
                )}
              >
                {trade.outcome ?? '—'}
              </td>
              <td
                className={clsx(
                  'py-2 pr-3 text-right tabular-nums',
                  pnl >= 0 ? 'text-pos' : 'text-neg',
                )}
              >
                {signedMoney(pnl)}
              </td>
              <td className="py-2 pr-3 text-right tabular-nums text-muted">
                {signedMoney(cumulative)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function HistoryTable({
  rows,
  onSelect,
  selectedId,
}: {
  rows: BacktestRun[]
  onSelect: (row: BacktestRun) => void
  selectedId: number | null
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[820px] border-collapse text-xs">
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
            const isSelected = selectedId === row.id
            return (
              <tr
                key={row.id}
                onClick={() => onSelect(row)}
                className={clsx(
                  'cursor-pointer border-b border-border/60 transition-colors last:border-0 hover:bg-white/5',
                  isSelected && 'bg-accent/10',
                )}
              >
                <td className="py-2 pr-3 text-slate-200">{row.strategy}</td>
                <td className="py-2 pr-3 text-muted">{row.symbol ?? '—'}</td>
                <td className="py-2 pr-3 text-muted">{row.timeframe ?? '—'}</td>
                <td className="py-2 pr-3 text-muted">
                  {formatDate(row.from_date)} → {formatDate(row.to_date)}
                </td>
                <td className="py-2 pr-3 text-right tabular-nums text-slate-200">
                  {fmtCount(toNumber(row.total_trades))}
                </td>
                <td className="py-2 pr-3 text-right tabular-nums text-slate-200">
                  {pctFromRatio(toNumber(row.win_rate))}
                </td>
                <td
                  className={clsx(
                    'py-2 pr-3 text-right tabular-nums',
                    net === null ? 'text-muted' : net > 0 ? 'text-pos' : net < 0 ? 'text-neg' : 'text-slate-200',
                  )}
                >
                  {signedMoney(net)}
                </td>
                <td className="py-2 pr-3 text-right tabular-nums text-muted">
                  {signedMoney(-Math.abs(toNumber(row.max_drawdown) ?? 0))}
                </td>
                <td className="py-2 pr-3 text-right tabular-nums text-muted">
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

function ResultsPanel({
  result,
  error,
  onRetry,
  fromHistory,
}: {
  result: BacktestResult | null
  error: string | null
  onRetry?: () => void
  fromHistory?: boolean
}) {
  if (error) {
    return <ErrorState message={error} onRetry={onRetry} />
  }

  if (!result) {
    return (
      <div className="flex min-h-[320px] flex-1 flex-col items-center justify-center rounded-md border border-dashed border-border bg-surface/60 p-8 text-center">
        <FlaskConical size={28} className="text-muted" />
        <p className="mt-3 text-sm text-slate-300">Configure a backtest on the left and click Run.</p>
        <p className="mt-1 max-w-sm text-xs text-muted">
          The results panel shows metrics, an equity curve and the full trade log.
        </p>
      </div>
    )
  }

  const trades = result.trade_log ?? []
  const metrics = resultMetrics(result)

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      {(fromHistory || result.run_id !== undefined) && (
        <p className="text-[11px] text-muted">
          {fromHistory ? 'Loaded from history' : `Run #${result.run_id ?? '—'}`}
          {result.candles_used ? ` · ${result.candles_used} candles` : ''}
          {` · ${result.strategy ?? ''} ${result.symbol ?? ''} ${result.timeframe ?? ''}`.trimEnd()}
        </p>
      )}

      <MetricsRow metrics={metrics} />

      {trades.length > 0 ? (
        <>
          <div className="rounded-md border border-border bg-surface p-3">
            <EquityCurve data={cumulativeCurve(trades)} color="#6366f1" height={240} />
          </div>

          <Section
            title="Trade log"
            subtitle={`${trades.length} simulated trades`}
            actions={
              <button
                type="button"
                onClick={() => {
                  const blob = new Blob([tradesToCsv(trades)], { type: 'text/csv;charset=utf-8' })
                  const url = URL.createObjectURL(blob)
                  const link = document.createElement('a')
                  link.href = url
                  link.download = `backtest-${result.strategy ?? 'run'}-${result.run_id ?? 'latest'}.csv`
                  link.click()
                  URL.revokeObjectURL(url)
                }}
                className="inline-flex items-center gap-1.5 rounded border border-border px-2 py-1 text-[11px] font-medium text-muted transition-colors hover:border-accent/50 hover:text-accent"
              >
                <Download size={12} />
                Export CSV
              </button>
            }
          >
            <TradeLogTable trades={trades} />
          </Section>
        </>
      ) : (
        <EmptyState
          title="The strategy produced no trades"
          hint="Widen the date range or loosen the parameters — this window had no qualifying setups."
        />
      )}
    </div>
  )
}

export function Backtest() {
  const strategies = useStrategies()
  const history = useBacktestHistory()
  const queryClient = useQueryClient()

  const [strategy, setStrategy] = useState('')
  const [symbol, setSymbol] = useState('XAUUSD')
  const [timeframe, setTimeframe] = useState<Timeframe>('M5')
  const [fromDate, setFromDate] = useState(monthAgoIso)
  const [toDate, setToDate] = useState(todayIso)
  const [params, setParams] = useState<Record<string, string>>({})
  const [tab, setTab] = useState<'config' | 'history'>('config')
  const [historyResult, setHistoryResult] = useState<BacktestResult | null>(null)
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null)

  const selected = useMemo(() => {
    const list = strategies.data ?? []
    if (list.length === 0) return ''
    return list.some((entry) => entry.name === strategy) ? strategy : list[0].name
  }, [strategies.data, strategy])

  const fields = PARAM_FIELDS[selected] ?? []

  const run = useMutation({
    mutationFn: async (): Promise<BacktestResult> => {
      const numericParams: Record<string, number> = {}
      for (const [key, raw] of Object.entries(params)) {
        const value = Number(raw)
        if (raw !== '' && Number.isFinite(value)) numericParams[key] = value
      }
      const body: BacktestRequest = {
        strategy: selected,
        symbol: symbol || 'XAUUSD',
        timeframe,
        from_date: fromDate,
        to_date: toDate,
        params: numericParams,
      }
      return postJson<BacktestResult, BacktestRequest>('/api/backtest/run', body, { timeout: 120_000 })
    },
    onSuccess: () => {
      setHistoryResult(null)
      void queryClient.invalidateQueries({ queryKey: ['backtest-history'] })
    },
  })

  // The endpoint answers HTTP 200 even when it fails, flagging `ok: false`.
  const apiFailure =
    run.data && run.data.ok === false ? (run.data.error ?? 'The backtest could not run.') : null
  const activeResult = historyResult ?? (run.data?.ok ? run.data : null)

  const loadHistoryRun = (row: BacktestRun) => {
    setSelectedRunId(row.id)
    setHistoryResult({
      ok: true,
      run_id: row.id,
      strategy: row.strategy,
      symbol: row.symbol ?? undefined,
      timeframe: row.timeframe ?? undefined,
      from_date: row.from_date ?? undefined,
      to_date: row.to_date ?? undefined,
      metrics: {
        total_trades: row.total_trades ?? 0,
        winners: row.winners ?? 0,
        win_rate: row.win_rate ?? 0,
        net_pnl: row.net_pnl ?? 0,
        max_drawdown: row.max_drawdown ?? 0,
        profit_factor: row.profit_factor,
        sharpe: row.sharpe ?? null,
      },
      trade_log: row.trade_log ?? [],
    })
  }

  const canRun = Boolean(selected) && !run.isPending

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-lg font-semibold">Backtesting</h1>
          <p className="mt-0.5 text-xs text-muted">
            Replay a strategy over historical candles and inspect its trade log.
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
          <aside className="w-full shrink-0 rounded-md border border-border bg-surface p-4 lg:w-[288px] lg:sticky lg:top-4">
            <h2 className="text-sm font-semibold text-slate-100">Run configuration</h2>

            <div className="mt-4 flex flex-col gap-3">
              <label className="flex flex-col gap-1">
                <span className={labelClass}>Strategy</span>
                <select
                  value={selected}
                  onChange={(event) => {
                    setStrategy(event.target.value)
                    setParams({})
                    setHistoryResult(null)
                  }}
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

              <div className="grid grid-cols-2 gap-2">
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
              </div>

              {fields.length > 0 && (
                <div className="flex flex-col gap-2 rounded-md border border-border bg-bg p-2.5">
                  <span className={labelClass}>Parameter overrides</span>
                  <div className="grid grid-cols-2 gap-2">
                    {fields.map((field) => (
                      <label key={field.key} className="flex flex-col gap-1">
                        <span className="text-[10px] text-muted">{field.label}</span>
                        <input
                          type="number"
                          step={field.step}
                          value={params[field.key] ?? ''}
                          onChange={(event) =>
                            setParams((prev) => ({ ...prev, [field.key]: event.target.value }))
                          }
                          placeholder="default"
                          className={inputClass}
                        />
                      </label>
                    ))}
                  </div>
                </div>
              )}

              <button
                type="button"
                onClick={() => run.mutate()}
                disabled={!canRun}
                className="mt-1 inline-flex w-full items-center justify-center gap-2 rounded-md bg-accent/80 px-3 py-2 text-xs font-semibold text-white transition-colors hover:bg-accent disabled:cursor-not-allowed disabled:opacity-60"
              >
                {run.isPending ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
                {run.isPending ? 'Running…' : 'Run backtest'}
              </button>

              {run.isPending && (
                <p className="text-[11px] text-muted">
                  Fetching candles and replaying the strategy — this can take a while.
                </p>
              )}

              <p className="text-[11px] text-muted">
                {selected
                  ? `${selected} · ${symbol || 'XAUUSD'} · ${timeframe}`
                  : 'Pick a strategy to configure a run.'}
              </p>
            </div>
          </aside>

          <ResultsPanel
            result={activeResult}
            error={
              apiFailure ??
              (run.isError ? errorMessage(run.error, 'The backtest request failed') : null)
            }
            onRetry={() => run.mutate()}
            fromHistory={Boolean(historyResult)}
          />
        </div>
      )}

      {tab === 'history' && (
        <Section title="Past runs" subtitle="Most recent backtests, newest first — click a row to load it">
          {history.isPending && <LoadingState label="Loading backtest history…" rows={3} />}
          {history.isError && (
            <ErrorState
              message={errorMessage(history.error, 'Could not load backtest history')}
              onRetry={() => void history.refetch()}
            />
          )}
          {history.isSuccess && (history.data?.length ?? 0) === 0 && (
            <EmptyState
              title="No backtest runs yet"
              hint="Run a backtest and every result is stored here for comparison."
            />
          )}
          {history.isSuccess && (history.data?.length ?? 0) > 0 && (
            <HistoryTable
              rows={history.data ?? []}
              onSelect={loadHistoryRun}
              selectedId={selectedRunId}
            />
          )}
        </Section>
      )}

      {tab === 'history' && historyResult && (
        <ResultsPanel result={historyResult} error={null} fromHistory />
      )}
    </div>
  )
}
