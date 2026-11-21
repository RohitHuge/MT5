import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { CHART_COLORS } from '../../lib/chartColors'
import { compactMoney, shortDate } from '../../lib/format'
import { EmptyState } from '../StateViews'

export interface EquitySeries {
  name: string
  color: string
  points: { date: string; cumulative_pnl: number }[]
}

interface MultiEquityCurveProps {
  series: EquitySeries[]
  height?: number
  emptyHint?: string
}

type MergedRow = { date: string } & Record<string, number | string | undefined>

/**
 * Overlaid cumulative P&L lines, one per strategy.
 *
 * Each strategy has its own set of trading dates, so the rows are merged on the
 * union of dates and missing points are left undefined (Recharts simply breaks
 * the line there instead of drawing a fake zero).
 */
export function MultiEquityCurve({ series, height = 280, emptyHint }: MultiEquityCurveProps) {
  const nonEmpty = series.filter((entry) => entry.points.length > 0)

  if (nonEmpty.length === 0) {
    return (
      <EmptyState
        title="No equity curves yet"
        hint={emptyHint ?? 'Curves appear once each strategy closes its first trade.'}
        className="h-[280px]"
      />
    )
  }

  const dates = Array.from(new Set(nonEmpty.flatMap((entry) => entry.points.map((p) => p.date)))).sort()

  const merged: MergedRow[] = dates.map((date) => {
    const row: MergedRow = { date }
    for (const entry of nonEmpty) {
      const point = entry.points.find((candidate) => candidate.date === date)
      if (point) row[entry.name] = point.cumulative_pnl
    }
    return row
  })

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={merged} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
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
        <Legend wrapperStyle={{ fontSize: 11, color: CHART_COLORS.muted }} />
        {nonEmpty.map((entry) => (
          <Line
            key={entry.name}
            type="monotone"
            dataKey={entry.name}
            stroke={entry.color}
            strokeWidth={2}
            dot={false}
            connectNulls
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  )
}
