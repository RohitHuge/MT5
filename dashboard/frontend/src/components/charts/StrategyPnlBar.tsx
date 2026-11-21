import { Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { CHART_COLORS } from '../../lib/chartColors'
import { compactMoney } from '../../lib/format'
import { EmptyState } from '../StateViews'

export interface StrategyPnlDatum {
  label: string
  net_pnl: number
}

interface StrategyPnlBarProps {
  data: StrategyPnlDatum[]
  height?: number
  emptyHint?: string
}

/**
 * Categorical bar chart: one bar per strategy, green/red by net P&L.
 * Kept separate from `DailyPnlBar` because the X axis is categorical
 * (display names) rather than a date series.
 */
export function StrategyPnlBar({ data, height = 240, emptyHint }: StrategyPnlBarProps) {
  if (!data || data.length === 0) {
    return (
      <EmptyState
        title="No P&L recorded yet"
        hint={emptyHint ?? 'Bars appear once a strategy has closed trades in this window.'}
        className="h-[240px]"
      />
    )
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={CHART_COLORS.border} strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="label"
          tick={{ fill: CHART_COLORS.muted, fontSize: 11 }}
          stroke={CHART_COLORS.border}
          interval={0}
        />
        <YAxis
          tickFormatter={compactMoney}
          tick={{ fill: CHART_COLORS.muted, fontSize: 11 }}
          stroke={CHART_COLORS.border}
          width={56}
        />
        <ReferenceLine y={0} stroke={CHART_COLORS.muted} strokeOpacity={0.5} />
        <Tooltip
          formatter={(value) => compactMoney(typeof value === 'number' ? value : Number(value))}
          contentStyle={{
            background: CHART_COLORS.surface,
            border: `1px solid ${CHART_COLORS.border}`,
            borderRadius: 6,
            fontSize: 12,
          }}
          labelStyle={{ color: CHART_COLORS.muted }}
          cursor={{ fill: '#ffffff0d' }}
        />
        <Bar dataKey="net_pnl" name="Net P&L" radius={[3, 3, 0, 0]} isAnimationActive={false}>
          {data.map((datum, index) => (
            <Cell
              key={`${datum.label}-${index}`}
              fill={datum.net_pnl >= 0 ? CHART_COLORS.pos : CHART_COLORS.neg}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}
