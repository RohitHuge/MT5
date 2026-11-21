import { isAxiosError } from 'axios'

/** Human-readable message for anything thrown by axios / fetch / JSON.parse. */
export function errorMessage(error: unknown, fallback = 'Request failed'): string {
  if (isAxiosError(error)) {
    const detail = (error.response?.data as { detail?: unknown } | undefined)?.detail
    if (typeof detail === 'string' && detail.trim() !== '') return detail
    if (error.code === 'ERR_NETWORK') return 'Cannot reach the dashboard API'
    if (error.message) return error.message
  }
  if (error instanceof Error && error.message) return error.message
  return fallback
}
