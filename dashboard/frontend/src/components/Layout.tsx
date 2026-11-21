import { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  LayoutDashboard,
  LineChart,
  FlaskConical,
  History,
  Radio,
  ChevronDown,
  ChevronRight,
} from 'lucide-react'
import { MT5StatusBadge } from './MT5StatusBadge'
import { AccountSummary } from './AccountSummary'
import { useAccountStatus } from '../hooks/useAccountStatus'
import { getJson } from '../lib/api'

const FALLBACK_STRATEGIES = [
  { name: 'ema-scalp', display_name: 'EMA Scalp' },
  { name: 'ob-fvg',    display_name: 'OB+FVG' },
  { name: 'liq-sweep', display_name: 'Liq Sweep' },
]

const NAV = [
  { to: '/', label: 'Overview', icon: LayoutDashboard },
  { to: '/live', label: 'Live Trading', icon: LineChart },
  { to: '/shadow', label: 'Shadow', icon: FlaskConical },
  { to: '/backtest', label: 'Backtest', icon: History },
  { to: '/signals', label: 'Signals', icon: Radio },
]

function navClass(isActive: boolean) {
  return [
    'flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors',
    isActive
      ? 'bg-accent/15 text-accent font-medium'
      : 'text-muted hover:bg-white/5 hover:text-slate-200',
  ].join(' ')
}

export function Layout() {
  useAccountStatus()
  const [strategiesOpen, setStrategiesOpen] = useState(true)
  const { data: strategiesData } = useQuery({
    queryKey: ['strategies-list'],
    queryFn: () => getJson<{ name: string; display_name: string }[]>('/api/strategies'),
    staleTime: 30_000,
    select: (rows) =>
      Array.isArray(rows) && rows.length > 0
        ? rows.map((r) => ({ name: r.name, display_name: r.display_name ?? r.name }))
        : FALLBACK_STRATEGIES,
  })
  const strategies = strategiesData ?? FALLBACK_STRATEGIES

  return (
    <div className="flex h-full min-h-screen flex-col">
      <header className="sticky top-0 z-20 flex h-14 items-center gap-6 border-b border-border bg-surface px-4">
        <span className="text-base font-bold whitespace-nowrap">MT5 Platform</span>
        <MT5StatusBadge />
        <div className="ml-auto">
          <AccountSummary />
        </div>
      </header>

      <div className="flex flex-1">
        <aside className="w-52 shrink-0 border-r border-border bg-[#141721] p-3">
          <nav className="flex flex-col gap-1">
            {NAV.slice(0, 2).map(({ to, label, icon: Icon }) => (
              <NavLink
                key={to}
                to={to}
                end={to === '/'}
                className={({ isActive }) => navClass(isActive)}
                title={label}
              >
                <Icon size={16} className="shrink-0" />
                <span className="truncate">{label}</span>
              </NavLink>
            ))}

            <button
              type="button"
              onClick={() => setStrategiesOpen((v) => !v)}
              className="flex items-center gap-2.5 rounded-md px-3 py-2 text-sm text-muted transition-colors hover:bg-white/5 hover:text-slate-200"
            >
              <LineChart size={16} className="shrink-0" />
              <span className="truncate">Strategies</span>
              {strategiesOpen ? (
                <ChevronDown size={14} className="ml-auto shrink-0" />
              ) : (
                <ChevronRight size={14} className="ml-auto shrink-0" />
              )}
            </button>

            {strategiesOpen && (
              <div className="ml-3 flex flex-col gap-0.5 border-l border-border pl-2">
                {strategies.map((s) => (
                  <NavLink
                    key={s.name}
                    to={`/strategy/${s.name}`}
                    className={({ isActive }) => navClass(isActive)}
                  >
                    <span className="truncate">{s.display_name}</span>
                  </NavLink>
                ))}
              </div>
            )}

            {NAV.slice(2).map(({ to, label, icon: Icon }) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) => navClass(isActive)}
                title={label}
              >
                <Icon size={16} className="shrink-0" />
                <span className="truncate">{label}</span>
              </NavLink>
            ))}
          </nav>
        </aside>

        <main className="flex-1 overflow-x-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
