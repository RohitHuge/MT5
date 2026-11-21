import { clsx } from 'clsx'

export interface Metric {
  label: string
  value: string
  /** Tailwind text class (`text-pos`, `text-neg`) or a `#hex` color. */
  color?: string
}

interface MetricsRowProps {
  metrics: Metric[]
  className?: string
}

function isHexColor(value: string): boolean {
  return /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i.test(value)
}

/** Responsive column count derived from the tile count. */
function defaultColumns(count: number): string {
  if (count <= 2) return 'grid-cols-2'
  if (count === 3) return 'grid-cols-2 md:grid-cols-3'
  if (count === 4) return 'grid-cols-2 md:grid-cols-4'
  return 'grid-cols-2 sm:grid-cols-3 lg:grid-cols-5'
}

/** Horizontal row of stat tiles used across every strategy view. */
export function MetricsRow({ metrics, className }: MetricsRowProps) {
  if (metrics.length === 0) {
    return <p className="text-xs text-muted">No metrics available.</p>
  }

  return (
    <div className={clsx('grid gap-3', defaultColumns(metrics.length), className)}>
      {metrics.map((metric) => (
        <div key={metric.label} className="rounded-md border border-border bg-surface px-3 py-2.5">
          <p className="text-[11px] tracking-wide text-muted uppercase">{metric.label}</p>
          <p
            className={clsx(
              'mt-1 text-lg font-semibold tabular-nums',
              !metric.color && 'text-slate-100',
              metric.color && (isHexColor(metric.color) ? undefined : metric.color),
            )}
            style={metric.color && isHexColor(metric.color) ? { color: metric.color } : undefined}
          >
            {metric.value}
          </p>
        </div>
      ))}
    </div>
  )
}
