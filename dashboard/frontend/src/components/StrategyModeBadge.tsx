import { clsx } from 'clsx'
import { MODE_HINT, MODE_LABEL, asStrategyMode } from '../lib/strategyMode'

const MODE_STYLES: Record<'live' | 'shadow' | 'off', string> = {
  live: 'bg-live/15 text-live border-live/40',
  shadow: 'bg-shadow/15 text-shadow border-shadow/40',
  off: 'bg-off/15 text-off border-off/40',
}

interface StrategyModeBadgeProps {
  mode: string | null | undefined
  className?: string
}

/** Pill badge: LIVE green / SHADOW blue / OFF grey. */
export function StrategyModeBadge({ mode, className }: StrategyModeBadgeProps) {
  const resolved = asStrategyMode(mode)

  return (
    <span
      title={MODE_HINT[resolved]}
      className={clsx(
        'inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[10px] font-semibold tracking-wider uppercase',
        MODE_STYLES[resolved],
        className,
      )}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {MODE_LABEL[resolved]}
    </span>
  )
}
