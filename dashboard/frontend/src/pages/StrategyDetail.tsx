import { useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { clsx } from 'clsx'
import {
  CandleChart,
  type Overlay,
  type SeriesMarker,
} from '../components/charts/CandleChart'
import { MetricsRow, type Metric } from '../components/charts/MetricsRow'
import { StrategyModeBadge } from '../components/StrategyModeBadge'
import { ModeToggle } from '../components/ModeToggle'
import { EmptyState, ErrorState, LoadingState, Section } from '../components/StateViews'
import { useSnapshots } from '../hooks/useSnapshots'
import { useStrategies } from '../hooks/useStrategies'
import { useStrategyPerf } from '../hooks/useStrategyPerf'
import { useVirtualTrades } from '../hooks/useVirtualTrades'
import { CHART_COLORS } from '../lib/chartColors'
import { errorMessage } from '../lib/errors'
import {
  formatDateTime,
  ratio,
  signedMoney,
  toNumber,
} from '../lib/format'
import { aggregatePerf, maxDrawdownOf } from '../lib/metrics'
import type { VirtualTradeRow } from '../types/api'

const PAGE_SIZE = 20

const OUTCOME_CLASS: Record<string, string> = {
  TP: 'text-pos',
  SL: 'text-neg',
  OPEN: 'text-shadow',
  FORCE_CLOSE: 'text-[#f59e0b]',
}

function toEpoch(value: string | null | undefined): number | null {
  if (!value) return null
  const ms = Date.parse(value)
  return Number.isNaN(ms) ? null : Math.floor(ms / 1000)
}

function numberAt(state: Record<string, unknown>, key: string): number | null {
  const value = state[key]
  return typeof value === 'number' ? value : null
}

/** Build chart overlays from one strategy's snapshot table. */
function buildOverlays(
  strategy: string,
  snapshots: ReadonlyArray<{ ts: string; indicators: Record<string, unknown> }>,
): Overlay[] {
  const ordered = [...snapshots].sort(
    (a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0),
  )
  const overlays: Overlay[] = []

  if (strategy === 'ema-scalp') {
    const specs: { key: string; label: string; color: string }[] = [
      { key: 'ema9', label: 'EMA9', color: '#eab308' },
      { key: 'ema21', label: 'EMA21', color: '#f59e0b' },
      { key: 'ema50', label: 'EMA50', color: CHART_COLORS.neg },
    ]
    for (const spec of specs) {
      const data = ordered
        .map((row) => {
          const time = toEpoch(row.ts)
          const value = numberAt(row.indicators, spec.key)
          return time !== null && value !== null ? { time, value } : null
        })
        .filter((p): p is { time: number; value: number } => p !== null)
      if (data.length) {
        overlays.push({ kind: 'ema', period: Number(spec.key.slice(3)), color: spec.color, data })
      }
    }
  }

  if (strategy === 'ob-fvg') {
    const zoneSets: { key: string; color: string }[] = [
      { key: 'ob_zones', color: CHART_COLORS.shadow },
      { key: 'fvg_zones', color: '#a855f7' },
    ]
    for (const set of zoneSets) {
      const zones = ordered.flatMap((row) => {
        const raw = row.indicators[set.key]
        if (!Array.isArray(raw)) return []
        return raw.flatMap((zone) => {
          if (!zone || typeof zone !== 'object') return []
          const entry = zone as Record<string, unknown>
          const time = toEpoch(entry.timeStart as string | undefined)
          const high = toNumber(entry.high)
          const low = toNumber(entry.low)
          if (time === null || high === null || low === null) return []
          return [{ timeStart: time, high, low }]
        })
      })
      if (zones.length) overlays.push({ kind: 'zones', color: set.color, zones })
    }
  }

  if (strategy === 'liq-sweep') {
    const latest = ordered[ordered.length - 1]?.indicators ?? {}
    for (const [key, color] of [
      ['pivot_highs', CHART_COLORS.pos],
      ['pivot_lows', CHART_COLORS.neg],
    ] as const) {
      const raw = latest[key]
      const levels = Array.isArray(raw)
        ? raw.map((v) => toNumber(v)).filter((v): v is number => v !== null)
        : []
      if (levels.length) overlays.push({ kind: 'hline', color, times: levels })
    }
  }

  return overlays
}

function buildMarkers(trades: ReadonlyArray<VirtualTradeRow>): Overlay[] {
  const markers: SeriesMarker[] = trades
    .map((trade): SeriesMarker | null => {
      const time = toEpoch(trade.signal_ts)
      if (time === null) return null
      const isBuy = trade.direction === 'BUY'
      return {
        time,
        position: isBuy ? 'belowBar' : 'aboveBar',
        color: isBuy ? CHART_COLORS.pos : CHART_COLORS.neg,
        shape: isBuy ? 'arrowUp' : 'arrowDown',
        text: trade.direction,
      }
    })
    .filter((m): m is SeriesMarker => m !== null)

  // lightweight-charts requires markers in ascending time order.
  markers.sort((a, b) => a.time - b.time)
  return markers.length ? [{ kind: 'markers', markers }] : []
}

function SignalHistoryTable({ strategy }: { strategy: string }) {
  const [page, setPage] = useState(0)
  const offset = page * PAGE_SIZE
  const trades = useVirtualTrades(strategy, PAGE_SIZE, offset)

  if (trades.isPending) return <LoadingState label="Loading signal history…" rows={4} />
  if (trades.isError) {
    return (
      <ErrorState
        message={errorMessage(trades.error, 'Could not load trade history')}
        onRetry={() => void trades.refetch()}
      />
    )
  }

  const rows = trades.data?.rows ?? []
  const total = trades.data?.total ?? 0
  if (rows.length === 0) {
    return (
      <EmptyState
        title="No signals recorded yet"
        hint="Rows appear here as soon as the strategy emits its first signal."
      />
    )
  }

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] border-collapse text-xs">
          <thead>
            <tr className="border-b border-border text-left text-[10px] tracking-wider text-muted uppercase">
              <th className="py-2 pr-3 font-medium">Date</th>
              <th className="py-2 pr-3 font-medium">Direction</th>
              <th className="py-2 pr-3 text-right font-medium">Entry</th>
              <th className="py-2 pr-3 text-right font-medium">SL</th>
              <th className="py-2 pr-3 text-right font-medium">TP</th>
              <th className="py-2 pr-3 font-medium">Outcome</th>
              <th className="py-2 pr-3 text-right font-medium">P&amp;L</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((trade) => {
              const pnl = toNumber(trade.net_pnl)
              return (
                <tr key={trade.id} className="border-b border-border/60 last:border-0">
                  <td className="py-2 pr-3 text-muted">
                    {formatDateTime(trade.signal_ts ?? trade.opened_at)}
                  </td>
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
                    {toNumber(trade.sl_price)?.toFixed(2) ?? '—'}
                  </td>
                  <td className="py-2 pr-3 text-right tabular-nums text-muted">
                    {toNumber(trade.tp_price)?.toFixed(2) ?? '—'}
                  </td>
                  <td className={clsx('py-2 pr-3 font-medium', OUTCOME_CLASS[trade.outcome] ?? '')}>
                    {trade.outcome}
                  </td>
                  <td
                    className={clsx(
                      'py-2 pr-3 text-right tabular-nums',
                      pnl === null ? 'text-muted' : pnl >= 0 ? 'text-pos' : 'text-neg',
                    )}
                  >
                    {signedMoney(pnl)}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <div className="mt-3 flex items-center gap-2 text-[11px] text-muted">
        <span>
          Page {page + 1} of {pageCount} · {total} signals
        </span>
        <div className="ml-auto flex gap-1">
          <button
            type="button"
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0}
            className="rounded border border-border px-2 py-1 transition-colors hover:border-accent/50 hover:text-accent disabled:opacity-40"
          >
            Previous
          </button>
          <button
            type="button"
            onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
            disabled={page >= pageCount - 1}
            className="rounded border border-border px-2 py-1 transition-colors hover:border-accent/50 hover:text-accent disabled:opacity-40"
          >
            Next
          </button>
        </div>
      </div>
    </div>
  )
}

/** Indicator chart, metrics and signal history for one strategy. */
export function StrategyDetail() {
  const { name } = useParams<{ name: string }>()
  const strategy = name ?? ''

  const strategies = useStrategies()
  const perf = useStrategyPerf(strategy, 30)
  const snapshots = useSnapshots(strategy, 200)
  // The marker overlay only needs the most recent trades; the table paginates
  // separately so this stays a fixed-size query.
  const recentTrades = useVirtualTrades(strategy, 200, 0)

  const meta = (strategies.data ?? []).find((s) => s.name === strategy)

  const overlays = useMemo<Overlay[]>(() => {
    const fromSnapshots = buildOverlays(
      strategy,
      (snapshots.data ?? []).map((row) => ({ ts: row.ts, indicators: row.indicators })),
    )
    const markers = buildMarkers(recentTrades.data?.rows ?? [])
    return [...fromSnapshots, ...markers]
  }, [strategy, snapshots.data, recentTrades.data])

  const hasIndicatorData = useMemo(
    () => overlays.some((overlay) => overlay.kind !== 'markers'),
    [overlays],
  )

  const metrics = useMemo<Metric[]>(() => {
    const rows = perf.data ?? []
    const agg = aggregatePerf(rows.map((row) => ({ ...row })))
    const trades = recentTrades.data?.rows ?? []
    const pnls = trades
      .map((t) => toNumber(t.net_pnl))
      .filter((v): v is number => v !== null)
    const winners = pnls.filter((v) => v > 0)
    const losers = pnls.filter((v) => v < 0)
    const avgWinner = winners.length ? winners.reduce((a, b) => a + b, 0) / winners.length : null
    const avgLoser = losers.length ? losers.reduce((a, b) => a + b, 0) / losers.length : null
    const curve = rows.map((row) => ({ date: row.date, cumulative_pnl: row.net_pnl }))
    const drawdown = rows.length ? maxDrawdownOf(curve) : 0

    return [
      { label: 'Win rate', value: agg.winRatePct === null ? '—' : `${agg.winRatePct.toFixed(1)}%` },
      { label: 'Total trades', value: String(agg.totalTrades) },
      {
        label: 'Net P&L',
        value: signedMoney(agg.netPnl),
        color: agg.netPnl >= 0 ? 'text-pos' : 'text-neg',
      },
      {
        label: 'Avg winner',
        value: signedMoney(avgWinner),
        color: avgWinner === null ? undefined : 'text-pos',
      },
      {
        label: 'Avg loser',
        value: signedMoney(avgLoser),
        color: avgLoser === null ? undefined : 'text-neg',
      },
      { label: 'Profit factor', value: ratio(agg.profitFactor) },
      {
        label: 'Max drawdown',
        value: signedMoney(-Math.abs(drawdown)),
        color: drawdown > 0 ? 'text-neg' : undefined,
      },
    ]
  }, [perf.data, recentTrades.data])

  if (!strategy) {
    return <EmptyState title="No strategy selected" hint="Pick a strategy from the sidebar." />
  }

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-center gap-3">
        <div>
          <h1 className="text-lg font-semibold">{meta?.display_name ?? strategy}</h1>
          <p className="mt-0.5 text-xs text-muted">
            {meta?.description || `Signal history and indicators for ${strategy}.`}
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <StrategyModeBadge mode={meta?.mode} />
          <ModeToggle name={strategy} mode={meta?.mode} displayName={meta?.display_name} />
        </div>
      </header>

      <Section title="Chart" subtitle={`XAUUSD · overlays from indicator snapshots`}>
        {snapshots.isError && (
          <ErrorState
            message={errorMessage(snapshots.error, 'Could not load indicator snapshots')}
            onRetry={() => void snapshots.refetch()}
          />
        )}
        {snapshots.isSuccess && !hasIndicatorData && (
          <p className="mb-2 rounded-md border border-border bg-bg px-3 py-2 text-[11px] text-muted">
            No indicator data yet — overlays appear once the strategy has processed candles.
          </p>
        )}
        <CandleChart symbol="XAUUSD" timeframe="M5" height={460} overlays={overlays} />
      </Section>

      <Section title="Metrics" subtitle="Last 30 days">
        {perf.isPending && <LoadingState label="Loading metrics…" rows={2} />}
        {perf.isError && (
          <ErrorState
            message={errorMessage(perf.error, 'Could not load performance')}
            onRetry={() => void perf.refetch()}
          />
        )}
        {perf.isSuccess && <MetricsRow metrics={metrics} />}
      </Section>

      <Section title="Signal history" subtitle="Individual trades, newest first">
        <SignalHistoryTable strategy={strategy} />
      </Section>
    </div>
  )
}
