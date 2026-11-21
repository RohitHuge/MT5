import { clsx } from 'clsx'

export interface RangeOption<T extends number> {
  days: T
  label: string
}

interface RangeSelectorProps<T extends number> {
  options: ReadonlyArray<RangeOption<T>>
  value: T
  onChange: (days: T) => void
  disabled?: boolean
  className?: string
}

/** Segmented 7d / 30d / 90d style range picker shared by Shadow + detail pages. */
export function RangeSelector<T extends number>({
  options,
  value,
  onChange,
  disabled,
  className,
}: RangeSelectorProps<T>) {
  return (
    <div
      className={clsx('inline-flex items-center gap-1 rounded-md border border-border bg-bg p-0.5', className)}
      role="group"
      aria-label="Date range"
    >
      {options.map((option) => {
        const active = option.days === value
        return (
          <button
            key={option.days}
            type="button"
            disabled={disabled}
            onClick={() => onChange(option.days)}
            aria-pressed={active}
            className={clsx(
              'rounded px-2.5 py-1 text-[11px] font-medium transition-colors',
              active ? 'bg-accent/20 text-accent' : 'text-muted hover:bg-white/5 hover:text-slate-200',
              disabled && 'cursor-not-allowed opacity-50',
            )}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}
