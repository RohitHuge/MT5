import { useMemo, useState } from 'react'
import { clsx } from 'clsx'
import { useQueries } from '@tanstack/react-query'
import { useStrategies } from '../hooks/useStrategies'
import { useCompare } from '../hooks/useCompare'
import {
  equityCurveQueryOptions,
  useEquityCurve,
} from '../hooks/useEquityCurve'
import { useLivePositions } from '../hooks/useLivePositions'
import { useStrategyPerf } from '../hooks/useStrategyPerf'
import { ModeToggle } from '../components/ModeToggle'
import { StrategyModeBadge } from '../components/StrategyModeBadge'
import { asStrategyMode } from '../lib/strategyMode'
import { RangeSelector, type RangeOption } from '../components/RangeSelector'
import { EmptyState, ErrorState, LoadingState, Section } from '../components/StateViews'
import { DailyPnlBar } from '../components/charts/DailyPnlBar'
import { EquityCurve } from '../components/charts/EquityCurve'
import { MetricsRow } from '../components/charts/MetricsRow'
import { MultiEquityCurve, type EquitySeries } from '../components/charts/MultiEquityCurve'
import { errorMessage } from '../lib/errors'
import { aggregatePerf, type PerfRowLike } from '../lib/metrics'
import { seriesColor } from '../lib/chartColors'
import {
  count as fmtCount,
  formatDateTime,
  money,
  pct,
  pnlTone,
  ratio,
  signedMoney,
  toNumber,
} from '../lib/format'
import type { CompareRow, Strategy, VirtualTrade } from '../types/api'

const RANGES: ReadonlyArray<RangeOption<number>> = [
  { days: 7, label: '7d' },
  { days: 30, label: '30d' },
  { days: 90, label: '90d' },
]

const OVERVIEW_TAB = 'overview'

type TabKey = typeof OVERVIEW_TAB | string

function ComparisonTable({ rows }: { rows: CompareRow[] }) {
  if (rows.length === 0) {
    return (
      <EmptyState
        title="No comparison data yet"
        hint="Once strategies close trades, their aggregates appear here."
      />
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] border-collapse text-xs">
        <thead>
          <tr className="border-b border-border text-left text-[10px] tracking-wider text-muted uppercase">
            <th className="py-2 pr-3 font-medium">Strategy</th>
            <th className="py-2 pr-3 font-medium">Mode</th>
            <th className="py-2 pr-3 text-right font-medium">Trades</th>
            <th className="py-2 pr-3 text-right font-medium">Win rate</th>
            <th className="py-2 pr-3 text-right font-medium">7d P&L</th>
            <th className="py-2 pr-3 text-right font-medium">30d P&L</th>
            <th className="py-2 text-right font-medium">All P&L</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.strategy} className="border-b border-border/60 last:border-0">
              <td className="py-2 pr-3 text-slate-200">{row.display_name || row.strategy}</td>
              <td className="py-2 pr-3">
                <StrategyModeBadge mode={asStrategyMode(row.mode)} />
              </td>
              <td className="py-2 pr-3 text-right text-slate-200 tabular-nums">
                {fmtCount(toNumber(row.total_trades))}
              </td>
              <td className="py-2 pr-3 text-right text-slate-200 tabular-nums">
                {pct(toNumber(row.win_rate_pct))}
              </td>
              <td
                className={`py-2 pr-3 text-right tabular-nums ${pnlTone(toNumber(row.week_net_pnl))}`}
              >
                {signedMoney(toNumber(row.week_net_pnl))}
              </td>
              <td
                className={`py-2 pr-3 text-right tabular-nums ${pnlTone(toNumber(row.month_net_pnl))}`}
              >
                {signedMoney(toNumber(row.month_net_pnl))}
              </td>
              <td
                className={`py-2 text-right tabular-nums ${pnlTone(toNumber(row.total_net_pnl))}`}
              >
                {signedMoney(toNumber(row.total_net_pnl))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function VirtualPositionsTable({
  positions,
  loading,
  error,
  onRetry,
}: {
  positions: VirtualTrade[]
  loading: boolean
  error: string | null
  onRetry: () => void
}) {
  if (loading) return <LoadingState label="Loading open positions…" rows={2} />
  if (error) return <ErrorState message={error} onRetry={onRetry} />
  if (positions.length === 0) {
    return (
      <EmptyState
        title="No open virtual positions"
        hint="Shadow trades appear here while they are open."
      />
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] border-collapse text-xs">
        <thead>
          <tr className="border-b border-border text-left text-[10px] tracking-wider text-muted uppercase">
            <th className="py-2 pr-3 font-medium">Direction</th>
            <th className="py-2 pr-3 text-right font-medium">Entry</th>
            <th className="py-2 pr-3 text-right font-medium">SL</th>
            <th className="py-2 pr-3 text-right font-medium">TP</th>
            <th className="py-2 pr-3 font-medium">Opened</th>
            <th className="py-2 pr-3 font-medium">Outcome</th>
            <th className="py-2 text-right font-medium">P&L</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((position) => {
            const pnl = toNumber(position.unrealised_pnl) ?? toNumber(position.net_pnl)
            const isBuy = position.direction?.toUpperCase() === 'BUY'
            return (
              <tr key={`${position.id}-${position.strategy}`} className="border-b border-border/60 last:border-0">
                <td className="py-2 pr-3">
                  <span
                    className={clsx(
                      'rounded px-1.5 py-0.5 text-[10px] font-semibold',
                      isBuy ? 'bg-pos/15 text-pos' : 'bg-neg/15 text-neg',
                    )}
                  >
                    {position.direction || '—'}
                  </span>
                </td>
                <td className="py-2 pr-3 text-right text-slate-200 tabular-nums">
                  {toNumber(position.entry_price)?.toFixed(2) ?? '—'}
                </td>
                <td className="py-2 pr-3 text-right text-muted tabular-nums">
                  {toNumber(position.sl_price)?.toFixed(2) ?? '—'}
                </td>
                <td className="py-2 pr-3 text-right text-muted tabular-nums">
                  {toNumber(position.tp_price)?.toFixed(2) ?? '—'}
                </td>
                <td className="py-2 pr-3 text-muted">{formatDateTime(position.opened_at)}</td>
                <td className="py-2 pr-3 text-muted">{position.outcome ?? 'OPEN'}</td>
                <td className={`py-2 text-right tabular-nums ${pnlTone(pnl)}`}>
                  {signedMoney(pnl)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function OverviewTab({ strategies, days }: { strategies: Strategy[]; days: number }) {
  const compare = useCompare(days)
  const curves = useQueries({
    queries: strategies.map((strategy) => equityCurveQueryOptions(strategy.name, days)),
  })

  const series: EquitySeries[] = strategies.map((strategy, index) => ({
    name: strategy.display_name || strategy.name,
    color: seriesColor(index),
    points: curves[index]?.data ?? [],
  }))

  const curvesLoading = curves.some((query) => query.isPending)
  const curvesError = curves.find((query) => query.isError)

  return (
    <div className="flex flex-col gap-6">
      <Section
        title="Comparison"
        subtitle={`Aggregated over the last ${days} days · week and month sub-totals included`}
      >
        {compare.isPending && <LoadingState label="Loading comparison…" rows={3} />}
        {compare.isError && (
          <ErrorState
            message={errorMessage(compare.error, 'Could not load comparison data')}
            onRetry={() => void compare.refetch()}
          />
        )}
        {compare.isSuccess && <ComparisonTable rows={compare.data ?? []} />}
      </Section>

      <Section title="Equity curves" subtitle="Cumulative net P&L, one line per strategy">
        {curvesLoading && <LoadingState label="Loading equity curves…" rows={2} />}
        {!curvesLoading && curvesError && (
          <ErrorState
            message={errorMessage(curvesError.error, 'Could not load equity curves')}
            onRetry={() => curves.forEach((query) => void query.refetch())}
          />
        )}
        {!curvesLoading && !curvesError && <MultiEquityCurve series={series} />}
      </Section>
    </div>
  )
}

function StrategyTab({ strategy, days }: { strategy: Strategy; days: number }) {
  const perf = useStrategyPerf(strategy.name, days)
  const curve = useEquityCurve(strategy.name, days)
  const positions = useLivePositions()

  const metrics = useMemo(() => {
    const rows: PerfRowLike[] = (perf.data ?? []).map((row) => ({
      date: row.date,
      trades: row.trades,
      winners: row.winners,
      losers: row.losers,
      net_pnl: row.net_pnl,
      win_rate: row.win_rate,
    }))
    const aggregate = aggregatePerf(rows)
    return [
      { label: 'Trades', value: fmtCount(aggregate.totalTrades) },
      {
        label: 'Win rate',
        value: pct(aggregate.winRatePct),
        color: aggregate.winRatePct === null ? undefined : 'text-slate-100',
      },
      {
        label: 'Net P&L',
        value: signedMoney(aggregate.netPnl),
        color: aggregate.netPnl > 0 ? 'text-pos' : aggregate.netPnl < 0 ? 'text-neg' : undefined,
      },
      { label: 'Profit factor', value: ratio(aggregate.profitFactor), color: 'text-slate-100' },
      {
        label: 'Max drawdown',
        value: aggregate.maxDrawdown > 0 ? `-${money(aggregate.maxDrawdown)}` : money(0),
        color: aggregate.maxDrawdown > 0 ? 'text-neg' : 'text-slate-100',
      },
    ]
  }, [perf.data])

  const shadowPositions = (positions.data?.shadow ?? []).filter(
    (position) => position.strategy === strategy.name,
  )

  const dailyPnl = (perf.data ?? []).map((row) => ({ date: row.date, net_pnl: row.net_pnl }))

  return (
    <div className="flex flex-col gap-6">
      <Section
        title={`${strategy.display_name || strategy.name} metrics`}
        subtitle={`Last ${days} days`}
        actions={
          // TEMP-VERIFY
          <div className="flex items-center gap-2">
            <StrategyModeBadge mode={asStrategyMode(strategy.mode)} />
            <ModeToggle name={strategy.name} mode={strategy.mode} compact />
          </div>
        }
      >
        {perf.isPending && <LoadingState label="Loading metrics…" rows={2} />}
        {perf.isError && (
          <ErrorState
            message={errorMessage(perf.error, 'Could not load strategy performance')}
            onRetry={() => void perf.refetch()}
          />
        )}
        {perf.isSuccess && (
          <>
            <MetricsRow metrics={metrics} />
            {!aggregateHasData(perf.data) && (
              <p className="mt-3 text-xs text-muted">
                No trades recorded for this strategy in the selected window.
              </p>
            )}
          </>
        )}
      </Section>

      <div className="grid gap-4 xl:grid-cols-2">
        <Section title="Daily P&L" subtitle="Green = profitable day, red = losing day">
          {perf.isPending && <LoadingState label="Loading daily P&L…" rows={2} />}
          {perf.isError && <ErrorState message={errorMessage(perf.error, 'Could not load daily P&L')} />}
          {perf.isSuccess && <DailyPnlBar data={dailyPnl} />}
        </Section>

        <Section title="Equity curve" subtitle="Cumulative net P&L">
          {curve.isPending && <LoadingState label="Loading equity curve…" rows={2} />}
          {curve.isError && (
            <ErrorState
              message={errorMessage(curve.error, 'Could not load equity curve')}
              onRetry={() => void curve.refetch()}
            />
          )}
          {curve.isSuccess && <EquityCurve data={curve.data ?? []} color="#6366f1" />}
        </Section>
      </div>

      <Section
        title="Open virtual positions"
        subtitle={
          positions.data
            ? `${shadowPositions.length} open · bridge ${positions.data.bridge_live ? 'live' : 'offline'}`
            : 'Open shadow trades for this strategy'
        }
      >
        <VirtualPositionsTable
          positions={shadowPositions}
          loading={positions.isPending}
          error={positions.isError ? errorMessage(positions.error, 'Could not load positions') : null}
          onRetry={() => void positions.refetch()}
        />
      </Section>
    </div>
  )
}

function aggregateHasData(rows: PerfRowLike[] | undefined): boolean {
  return (rows ?? []).some((row) => row.trades > 0)
}

export function ShadowTesting() {
  const strategies = useStrategies()
  const [days, setDays] = useState<number>(30)
  const [tab, setTab] = useState<TabKey>('ob-fvg') // TEMP-VERIFY

  const strategyList = strategies.data ?? []
  const activeStrategy = strategyList.find((strategy) => strategy.name === tab)

  // A strategy can disappear (or a deep link can name a stale one) — fall back.
  const activeTab: TabKey = activeStrategy ? tab : OVERVIEW_TAB

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-lg font-semibold">Shadow Testing</h1>
          <p className="mt-0.5 text-xs text-muted">
            Compare strategies on virtual trades before promoting one to live.
          </p>
        </div>
        <RangeSelector
          options={RANGES}
          value={days}
          onChange={setDays}
          className="ml-auto"
          disabled={strategies.isPending}
        />
      </header>

      {strategies.isPending && <LoadingState label="Loading strategies…" rows={2} />}
      {strategies.isError && (
        <ErrorState
          message={errorMessage(strategies.error, 'Could not load strategies')}
          onRetry={() => void strategies.refetch()}
        />
      )}

      {strategies.isSuccess && (
        <>
          <nav className="flex flex-wrap gap-1 border-b border-border">
            <button
              type="button"
              onClick={() => setTab(OVERVIEW_TAB)}
              aria-pressed={activeTab === OVERVIEW_TAB}
              className={clsx(
                '-mb-px border-b-2 px-3 py-2 text-xs font-medium transition-colors',
                activeTab === OVERVIEW_TAB
                  ? 'border-accent text-accent'
                  : 'border-transparent text-muted hover:text-slate-200',
              )}
            >
              Overview
            </button>
            {strategyList.map((strategy) => (
              <button
                key={strategy.name}
                type="button"
                onClick={() => setTab(strategy.name)}
                aria-pressed={activeTab === strategy.name}
                className={clsx(
                  '-mb-px border-b-2 px-3 py-2 text-xs font-medium transition-colors',
                  activeTab === strategy.name
                    ? 'border-accent text-accent'
                    : 'border-transparent text-muted hover:text-slate-200',
                )}
              >
                {strategy.display_name || strategy.name}
              </button>
            ))}
          </nav>

          {strategyList.length === 0 && (
            <EmptyState
              title="No strategies configured"
              hint="Seed the strategies table to see shadow comparisons."
            />
          )}

          {strategyList.length > 0 && activeTab === OVERVIEW_TAB && (
            <OverviewTab strategies={strategyList} days={days} />
          )}

          {activeStrategy && (
            <StrategyTab key={activeStrategy.name} strategy={activeStrategy} days={days} />
          )}
        </>
      )}
    </div>
  )
}
