import { Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { CHART_COLORS } from '../../lib/chartColors'
import { compactMoney, shortDate } from '../../lib/format'
import { EmptyState } from '../StateViews'

export interface DailyPnlDatum {
  date: string
  net_pnl: number
}

interface DailyPnlBarProps {
  data: DailyPnlDatum[]
  height?: number
  emptyHint?: string
}

/** Daily net P&L bars: green when positive, red when negative. */
export function DailyPnlBar({ data, height = 220, emptyHint }: DailyPnlBarProps) {
  if (!data || data.length === 0) {
    return (
      <EmptyState
        title="No daily results yet"
        hint={emptyHint ?? 'Daily bars appear once trades are recorded for the selected range.'}
        className="h-[220px]"
      />
    )
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={CHART_COLORS.border} strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="date"
          tickFormatter={shortDate}
          tick={{ fill: CHART_COLORS.muted, fontSize: 11 }}
          stroke={CHART_COLORS.border}
          minTickGap={16}
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
          labelFormatter={(label) => shortDate(typeof label === 'string' ? label : String(label))}
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
          {data.map((datum) => (
            <Cell
              key={`${datum.date}-${datum.net_pnl}`}
              fill={datum.net_pnl >= 0 ? CHART_COLORS.pos : CHART_COLORS.neg}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}
