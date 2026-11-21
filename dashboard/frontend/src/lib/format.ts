/** Small, allocation-light formatting helpers shared by every page.
 *
 *  Every helper accepts `null | undefined` because almost every aggregate field
 *  coming back from the dashboard API can be null (empty tables, no trades yet).
 */

export function isNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

export function toNumber(value: unknown): number | null {
  if (isNumber(value)) return value
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

export function money(value: number | null | undefined, digits = 2): string {
  if (!isNumber(value)) return '—'
  return `$${value.toFixed(digits)}`
}

export function signedMoney(value: number | null | undefined, digits = 2): string {
  if (!isNumber(value)) return '—'
  const sign = value > 0 ? '+' : value < 0 ? '-' : ''
  return `${sign}$${Math.abs(value).toFixed(digits)}`
}

/** Tailwind text class for a P&L-style number. */
export function pnlTone(value: number | null | undefined): string {
  if (!isNumber(value) || value === 0) return 'text-slate-200'
  return value > 0 ? 'text-pos' : 'text-neg'
}

/** Percentage that is already expressed as 0–100 (e.g. `win_rate_pct`). */
export function pct(value: number | null | undefined, digits = 1): string {
  if (!isNumber(value)) return '—'
  return `${value.toFixed(digits)}%`
}

/** Percentage expressed as a 0–1 ratio (e.g. `daily_summary.win_rate`). */
export function pctFromRatio(value: number | null | undefined, digits = 1): string {
  if (!isNumber(value)) return '—'
  const scaled = value <= 1 ? value * 100 : value
  return `${scaled.toFixed(digits)}%`
}

export function count(value: number | null | undefined): string {
  if (!isNumber(value)) return '—'
  return String(Math.round(value))
}

export function ratio(value: number | null | undefined, digits = 2): string {
  if (!isNumber(value)) return '—'
  return value.toFixed(digits)
}

export function parseDate(value: string | number | null | undefined): Date | null {
  if (value === null || value === undefined || value === '') return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

/** `2025-01-31` → `31 Jan` (chart axis labels). */
export function shortDate(value: string | null | undefined): string {
  const date = parseDate(value)
  if (!date) return typeof value === 'string' ? value : '—'
  return date.toLocaleDateString(undefined, { day: '2-digit', month: 'short' })
}

export function formatDate(value: string | null | undefined): string {
  const date = parseDate(value)
  if (!date) return '—'
  return date.toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' })
}

export function formatTime(value: string | number | null | undefined): string {
  const date = parseDate(value)
  if (!date) return '—'
  return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export function formatDateTime(value: string | number | null | undefined): string {
  const date = parseDate(value)
  if (!date) return '—'
  return date.toLocaleString(undefined, {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** Compact money for chart ticks: `$1.2k`, `-$340`. */
export function compactMoney(value: number | null | undefined): string {
  if (!isNumber(value)) return '—'
  const abs = Math.abs(value)
  if (abs >= 1_000_000) return `${value < 0 ? '-' : ''}$${(abs / 1_000_000).toFixed(1)}m`
  if (abs >= 1_000) return `${value < 0 ? '-' : ''}$${(abs / 1_000).toFixed(1)}k`
  return `${value < 0 ? '-' : ''}$${abs.toFixed(0)}`
}
