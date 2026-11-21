import { useId } from 'react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { CHART_COLORS } from '../../lib/chartColors'
import { compactMoney, shortDate } from '../../lib/format'
import { EmptyState } from '../StateViews'

export interface EquityPoint {
  date: string
  cumulative_pnl: number
}

interface EquityCurveProps {
  data: EquityPoint[]
  color: string
  height?: number
  emptyHint?: string
}

/**
 * Single-series cumulative P&L area chart.
 *
 * Renders a clean empty state (not an empty grid) when there is no data —
 * fresh strategies legitimately have a zero-row equity curve.
 */
export function EquityCurve({ data, color, height = 220, emptyHint }: EquityCurveProps) {
  const gradientId = useId().replace(/:/g, '')

  if (!data || data.length === 0) {
    return (
      <EmptyState
        title="No equity data yet"
        hint={emptyHint ?? 'The curve appears once the strategy closes its first trade.'}
        className="h-[220px]"
      />
    )
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.35} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke={CHART_COLORS.border} strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="date"
          tickFormatter={shortDate}
          tick={{ fill: CHART_COLORS.muted, fontSize: 11 }}
          stroke={CHART_COLORS.border}
          minTickGap={24}
        />
        <YAxis
          tickFormatter={compactMoney}
          tick={{ fill: CHART_COLORS.muted, fontSize: 11 }}
          stroke={CHART_COLORS.border}
          width={56}
        />
        <Tooltip
          formatter={(value) => compactMoney(typeof value === 'number' ? value : Number(value))}
          labelFormatter={(label) => shortDate(typeof label === 'string' ? label : String(label))}
          contentStyle={{
            background: CHART_COLORS.surface,
            border: `1px solid ${CHART_COLORS.border}`,
            borderRadius: 6,
            fontSize: 12,
          }}
          labelStyle={{ color: CHART_COLORS.muted }}
        />
        <Area
          type="monotone"
          dataKey="cumulative_pnl"
          name="Cumulative P&L"
          stroke={color}
          strokeWidth={2}
          fill={`url(#${gradientId})`}
          dot={false}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}
