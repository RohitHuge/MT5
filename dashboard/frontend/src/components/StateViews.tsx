import type { ReactNode } from 'react'
import { clsx } from 'clsx'

interface LoadingStateProps {
  label?: string
  rows?: number
  className?: string
}

/** Skeleton block used while a query is pending. */
export function LoadingState({ label = 'Loading…', rows = 3, className }: LoadingStateProps) {
  return (
    <div className={clsx('flex flex-col gap-2', className)} role="status" aria-live="polite">
      <span className="text-xs text-muted">{label}</span>
      {Array.from({ length: rows }, (_, index) => (
        <div
          key={index}
          className="h-12 animate-pulse rounded-md border border-border bg-surface"
        />
      ))}
    </div>
  )
}

interface ErrorStateProps {
  message: string
  onRetry?: () => void
  className?: string
}

/** Error panel with an optional retry action. */
export function ErrorState({ message, onRetry, className }: ErrorStateProps) {
  return (
    <div
      className={clsx(
        'rounded-md border border-neg/40 bg-neg/10 px-4 py-3 text-xs',
        className,
      )}
      role="alert"
    >
      <p className="font-medium text-neg">Could not load data</p>
      <p className="mt-1 text-muted">{message}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-2 rounded-md border border-border bg-surface px-2.5 py-1 text-xs text-slate-200 transition-colors hover:border-accent/50 hover:text-accent"
        >
          Retry
        </button>
      )}
    </div>
  )
}

interface EmptyStateProps {
  title?: string
  hint?: string
  className?: string
}

/** Neutral "nothing here yet" panel. */
export function EmptyState({ title = 'No data yet', hint, className }: EmptyStateProps) {
  return (
    <div
      className={clsx(
        'flex flex-col items-center justify-center rounded-md border border-dashed border-border bg-surface/60 px-4 py-8 text-center',
        className,
      )}
    >
      <p className="text-sm text-slate-300">{title}</p>
      {hint && <p className="mt-1 max-w-sm text-xs text-muted">{hint}</p>}
    </div>
  )
}

interface SectionProps {
  title: string
  subtitle?: string
  actions?: ReactNode
  children: ReactNode
  className?: string
}

/** Titled card wrapper matching the Phase 1 visual language. */
export function Section({ title, subtitle, actions, children, className }: SectionProps) {
  return (
    <section className={clsx('rounded-md border border-border bg-surface', className)}>
      <div className="flex items-start gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-slate-100">{title}</h2>
          {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
        </div>
        {actions && <div className="ml-auto shrink-0">{actions}</div>}
      </div>
      <div className="p-4">{children}</div>
    </section>
  )
}
