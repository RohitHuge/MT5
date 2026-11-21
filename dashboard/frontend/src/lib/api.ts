import axios from 'axios'

export const api = axios.create({
  baseURL: '',
  timeout: 10000,
})

export async function getJson<T>(url: string): Promise<T> {
  const { data } = await api.get<T>(url)
  return data
}

export async function postJson<T, B = unknown>(url: string, body: B, config?: import('axios').AxiosRequestConfig): Promise<T> {
  const { data } = await api.post<T>(url, body, config)
  return data
}

/**
 * Defensive list coercion for endpoints that may answer with a bare array or
 * wrap it in an object (`{items: [...]}`, `{rows: [...]}`, `{history: [...]}`).
 * The dashboard API returns bare arrays today; this keeps pages from crashing
 * on a wrapper or an error envelope.
 */
export function asArray<T>(payload: unknown): T[] {
  if (Array.isArray(payload)) return payload as T[]
  if (payload && typeof payload === 'object') {
    for (const key of ['items', 'rows', 'history', 'data', 'results']) {
      const value = (payload as Record<string, unknown>)[key]
      if (Array.isArray(value)) return value as T[]
    }
  }
  return []
}
