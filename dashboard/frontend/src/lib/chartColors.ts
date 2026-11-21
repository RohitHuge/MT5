/**
 * Chart palette. Values mirror `tailwind.config.js` so SVGs (which cannot use
 * Tailwind classes for `fill`/`stroke`) stay in sync with the palette.
 */
export const CHART_COLORS = {
  bg: '#0f1117',
  surface: '#1a1d27',
  border: '#2a2d3a',
  muted: '#94a3b8',
  pos: '#22c55e',
  neg: '#ef4444',
  accent: '#6366f1',
  shadow: '#3b82f6',
} as const

/** Stable per-strategy colors for overlaid series. */
export const SERIES_COLORS = [
  '#6366f1',
  '#22c55e',
  '#f59e0b',
  '#3b82f6',
  '#a855f7',
  '#14b8a6',
  '#ef4444',
  '#eab308',
] as const

export function seriesColor(index: number): string {
  return SERIES_COLORS[index % SERIES_COLORS.length]
}
