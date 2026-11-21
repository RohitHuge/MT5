import type { StrategyMode } from '../types/api'

/** Accepts loose strings from the API / WebSocket payloads. */
export function asStrategyMode(value: unknown): StrategyMode {
  const normalised = typeof value === 'string' ? value.trim().toLowerCase() : ''
  if (normalised === 'live' || normalised === 'shadow' || normalised === 'off') return normalised
  return 'off'
}

export const MODE_LABEL: Record<StrategyMode, string> = {
  live: 'LIVE',
  shadow: 'SHADOW',
  off: 'OFF',
}

/** Explanation surfaced as the badge's `title`. */
export const MODE_HINT: Record<StrategyMode, string> = {
  live: 'Placing real orders on the MT5 account',
  shadow: 'Recording virtual trades only',
  off: 'Not evaluating signals',
}
