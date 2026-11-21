import { useNavigate } from 'react-router-dom'
import { ArrowDownRight, ArrowUpRight, Minus } from 'lucide-react'
import { useCompare } from '../hooks/useCompare'
import { useStrategies } from '../hooks/useStrategies'
import { useMT5Store } from '../store/mt5Store'
import { StrategyModeBadge } from '../components/StrategyModeBadge'
import { asStrategyMode } from '../lib/strategyMode'
import { EmptyState, ErrorState, LoadingState, Section } from '../components/StateViews'
import { StrategyPnlBar, type StrategyPnlDatum } from '../components/charts/StrategyPnlBar'
import { errorMessage } from '../lib/errors'
import { count as fmtCount, money, pct, pnlTone, signedMoney, toNumber } from '../lib/format'
import type { CompareRow, Strategy } from '../types/api'

const WEEK_DAYS = 7

function StrategyCard({ strategy }: { strategy: Strategy }) {
  const navigate = useNavigate()
  const todayPnl = toNumber(strategy.today_pnl)
  const todayTrades = toNumber(strategy.today_trades)
  const winRate = toNumber(strategy.today_win_rate)

  return (
    <button
      type="button"
      onClick={() => navigate(`/strategy/${strategy.name}`)}
      className="group flex flex-col gap-3 rounded-md border border-border bg-surface p-4 text-left transition-colors hover:border-accent/50"
    >
      <div className="flex items-start gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-slate-100 group-hover:text-accent">
            {strategy.display_name || strategy.name}
          </p>
          <p className="mt-0.5 truncate text-[11px] text-muted">
            {strategy.description ?? strategy.name}
          </p>
        </div>
        <StrategyModeBadge mode={asStrategyMode(strategy.mode)} className="ml-auto shrink-0" />
      </div>

      <div className="grid grid-cols-3 gap-2 border-t border-border pt-3">
        <div>
          <p className="text-[10px] tracking-wide text-muted uppercase">Today</p>
          <p className={`mt-0.5 text-sm font-semibold tabular-nums ${pnlTone(todayPnl)}`}>
            {signedMoney(todayPnl)}
          </p>
        </div>
        <div>
          <p className="text-[10px] tracking-wide text-muted uppercase">Trades</p>
          <p className="mt-0.5 text-sm font-semibold text-slate-200 tabular-nums">
            {fmtCount(todayTrades)}
          </p>
        </div>
        <div>
          <p className="text-[10px] tracking-wide text-muted uppercase">Win rate</p>
          <p className="mt-0.5 text-sm font-semibold text-slate-200 tabular-nums">
            {winRate === null ? '—' : pct(winRate <= 1 ? winRate * 100 : winRate)}
          </p>
        </div>
      </div>
    </button>
  )
}

function SnapshotCard({
  label,
  value,
  tone,
  trend,
}: {
  label: string
  value: string
  tone?: string
  trend?: 'up' | 'down' | 'flat'
}) {
  const TrendIcon = trend === 'up' ? ArrowUpRight : trend === 'down' ? ArrowDownRight : Minus
  return (
    <div className="rounded-md border border-border bg-surface px-4 py-3">
      <p className="text-[11px] tracking-wide text-muted uppercase">{label}</p>
      <div className="mt-1 flex items-baseline gap-2">
        <span className={`text-xl font-semibold tabular-nums ${tone ?? 'text-slate-100'}`}>
          {value}
        </span>
        {trend && (
          <TrendIcon
            size={16}
            className={trend === 'up' ? 'text-pos' : trend === 'down' ? 'text-neg' : 'text-muted'}
          />
        )}
      </div>
    </div>
  )
}

export function Overview() {
  const strategies = useStrategies()
  const compare = useCompare(WEEK_DAYS)
  const account = useMT5Store((s) => s.account)

  const weeklyData: StrategyPnlDatum[] = (compare.data ?? [])
    .map((row: CompareRow) => ({
      label: row.display_name || row.strategy,
      net_pnl: toNumber(row.week_net_pnl) ?? 0,
    }))
    .slice()
    .sort((a, b) => b.net_pnl - a.net_pnl)

  const equity = toNumber(account?.equity)
  const balance = toNumber(account?.balance)
  const trend: 'up' | 'down' | 'flat' =
    equity === null || balance === null ? 'flat' : equity > balance ? 'up' : equity < balance ? 'down' : 'flat'

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-lg font-semibold">Overview</h1>
        <p className="mt-0.5 text-xs text-muted">
          Strategy performance at a glance — refreshed every 60 seconds.
        </p>
      </header>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-slate-100">Strategies</h2>
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
            hint="Seed the strategies table to see performance cards here."
          />
        )}
        {strategies.isSuccess && (strategies.data?.length ?? 0) > 0 && (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {strategies.data.map((strategy) => (
              <StrategyCard key={strategy.name} strategy={strategy} />
            ))}
          </div>
        )}
      </section>

      <Section
        title={`This week's P&L (${WEEK_DAYS}d)`}
        subtitle="Net profit and loss per strategy for the last seven days"
      >
        {compare.isPending && <LoadingState label="Loading weekly comparison…" rows={2} />}
        {compare.isError && (
          <ErrorState
            message={errorMessage(compare.error, 'Could not load comparison data')}
            onRetry={() => void compare.refetch()}
          />
        )}
        {compare.isSuccess && <StrategyPnlBar data={weeklyData} />}
      </Section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-slate-100">Account snapshot</h2>
        {!account && (
          <EmptyState
            title="Account data unavailable"
            hint="The MT5 bridge is offline, so balance, equity and margin cannot be read."
          />
        )}
        {account && (
          <div className="grid gap-3 sm:grid-cols-3">
            <SnapshotCard label="Balance" value={money(toNumber(account.balance))} />
            <SnapshotCard
              label="Equity"
              value={money(equity)}
              tone={pnlTone(equity !== null && balance !== null ? equity - balance : null)}
              trend={trend}
            />
            <SnapshotCard label="Free margin" value={money(toNumber(account.free_margin))} />
          </div>
        )}
      </section>
    </div>
  )
}
