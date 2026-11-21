import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { toNumber } from '../lib/format'
import type { RawSignal, SignalConnectionStatus, SignalEvent } from '../types/api'

const MAX_SIGNALS = 50
const BACKOFF_START_MS = 1_000
const BACKOFF_MAX_MS = 30_000

function signalsUrl(): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws/signals`
}

function pick<T>(source: RawSignal, keys: string[]): T | null {
  for (const key of keys) {
    const value = source[key]
    if (value !== undefined && value !== null && value !== '') return value as T
  }
  return null
}

/**
 * Resolve a possibly-unix-seconds / unix-millis / ISO timestamp to ISO text.
 * The feed has historically carried all three.
 */
function normaliseTimestamp(raw: unknown): string | null {
  const value = toNumber(raw)
  if (value !== null) {
    const millis = value > 1e12 ? value : value * 1000
    const date = new Date(millis)
    return Number.isNaN(date.getTime()) ? null : date.toISOString()
  }
  if (typeof raw === 'string') {
    const date = new Date(raw)
    return Number.isNaN(date.getTime()) ? null : date.toISOString()
  }
  return null
}

let sequence = 0

/**
 * Normalise a loosely-typed signal payload into a `SignalEvent`.
 * Returns `null` for keepalives and payloads with no usable identity.
 */
export function parseSignal(raw: RawSignal, receivedAt = Date.now()): SignalEvent | null {
  if (!raw || typeof raw !== 'object') return null
  if (raw.type === 'ping' || raw.type === 'pong' || raw.type === 'heartbeat') return null

  const strategyRaw = pick<unknown>(raw, ['strategy', 'strategy_name', 'strategyName'])
  const directionRaw = pick<unknown>(raw, ['direction', 'signal', 'side'])
  const symbolRaw = pick<unknown>(raw, ['symbol', 'ticker'])

  const strategy = typeof strategyRaw === 'string' ? strategyRaw : ''
  if (!strategy) return null

  const directionText =
    typeof directionRaw === 'string' ? directionRaw.trim().toUpperCase() : ''
  const direction: SignalEvent['direction'] =
    directionText === 'BUY' || directionText === 'LONG'
      ? 'BUY'
      : directionText === 'SELL' || directionText === 'SHORT'
        ? 'SELL'
        : 'UNKNOWN'

  const timestamp = normaliseTimestamp(
    pick<unknown>(raw, ['timestamp', 'ts', 'time', 'candle_ts', 'emitted_at', 'created_at']) ??
      null,
  )

  sequence += 1

  return {
    id: `${receivedAt}-${sequence}`,
    strategy,
    direction,
    symbol: typeof symbolRaw === 'string' && symbolRaw ? symbolRaw : 'XAUUSD',
    entryPrice: toNumber(pick<unknown>(raw, ['entry_price', 'price', 'entry', 'entryPrice'])),
    sl: toNumber(pick<unknown>(raw, ['sl', 'stop_loss', 'sl_price', 'stopLoss'])),
    tp: toNumber(pick<unknown>(raw, ['tp', 'take_profit', 'tp_price', 'takeProfit'])),
    lots: toNumber(pick<unknown>(raw, ['lots', 'volume', 'size'])),
    confidence: toNumber(pick<unknown>(raw, ['confidence', 'conf'])),
    mode: typeof raw.mode === 'string' ? raw.mode : null,
    comment: typeof raw.comment === 'string' && raw.comment ? raw.comment : null,
    timestamp,
    receivedAt,
  }
}

interface UseSignalFeedOptions {
  /** Pause live insertion (user scrolled up); incoming signals are buffered. */
  paused?: boolean
  maxSignals?: number
}

interface UseSignalFeedResult {
  signals: SignalEvent[]
  status: SignalConnectionStatus
  /** Signals received while paused and not yet shown. */
  pendingCount: number
  /** Drop the current buffer (useful for a "clear" action). */
  clear: () => void
  /** Force an immediate reconnect attempt. */
  reconnect: () => void
}

/**
 * `/ws/signals` client: normalises payloads, keeps the last N newest-first,
 * ignores keepalives, and reconnects with exponential backoff capped at 30s.
 */
export function useSignalFeed({
  paused = false,
  maxSignals = MAX_SIGNALS,
}: UseSignalFeedOptions = {}): UseSignalFeedResult {
  const [signals, setSignals] = useState<SignalEvent[]>([])
  const [status, setStatus] = useState<SignalConnectionStatus>('connecting')
  const [pendingCount, setPendingCount] = useState(0)

  const socketRef = useRef<WebSocket | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const backoffRef = useRef(BACKOFF_START_MS)
  const pausedRef = useRef(paused)
  const bufferedRef = useRef<SignalEvent[]>([])
  const [reconnectToken, setReconnectToken] = useState(0)

  useEffect(() => {
    pausedRef.current = paused

    // Draining on resume keeps the feed continuous instead of jumping.
    // `bufferedRef` is already newest-first, so it simply prepends.
    if (!paused && bufferedRef.current.length > 0) {
      const flushed = bufferedRef.current
      bufferedRef.current = []
      setPendingCount(0)
      setSignals((prev) => [...flushed, ...prev].slice(0, maxSignals))
    }
  }, [paused, maxSignals])

  const ingest = useCallback(
    (event: SignalEvent) => {
      if (pausedRef.current) {
        bufferedRef.current = [event, ...bufferedRef.current].slice(0, maxSignals)
        setPendingCount(bufferedRef.current.length)
        return
      }
      setSignals((prev) => [event, ...prev].slice(0, maxSignals))
    },
    [maxSignals],
  )

  useEffect(() => {
    let disposed = false

    function connect() {
      if (disposed) return
      setStatus('connecting')

      let socket: WebSocket
      try {
        socket = new WebSocket(signalsUrl())
      } catch {
        setStatus('disconnected')
        scheduleReconnect()
        return
      }
      socketRef.current = socket

      socket.onopen = () => {
        backoffRef.current = BACKOFF_START_MS
        setStatus('connected')
      }

      socket.onmessage = (message: MessageEvent<unknown>) => {
        if (typeof message.data !== 'string') return
        let payload: RawSignal
        try {
          payload = JSON.parse(message.data) as RawSignal
        } catch {
          return
        }
        const event = parseSignal(payload)
        if (event) ingest(event)
      }

      socket.onerror = () => {
        // `onclose` always follows; let it own the reconnect schedule.
      }

      socket.onclose = () => {
        socketRef.current = null
        if (disposed) return
        setStatus('disconnected')
        scheduleReconnect()
      }
    }

    function scheduleReconnect() {
      if (disposed) return
      const delay = backoffRef.current
      backoffRef.current = Math.min(backoffRef.current * 2, BACKOFF_MAX_MS)
      timerRef.current = setTimeout(connect, delay)
    }

    connect()

    return () => {
      disposed = true
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current)
        timerRef.current = null
      }
      const socket = socketRef.current
      socketRef.current = null
      if (socket) {
        socket.onopen = null
        socket.onmessage = null
        socket.onerror = null
        socket.onclose = null
        socket.close()
      }
    }
  }, [ingest, reconnectToken])

  const clear = useCallback(() => {
    bufferedRef.current = []
    setPendingCount(0)
    setSignals([])
  }, [])

  const reconnect = useCallback(() => {
    backoffRef.current = BACKOFF_START_MS
    setReconnectToken((token) => token + 1)
  }, [])

  return useMemo(
    () => ({ signals, status, pendingCount, clear, reconnect }),
    [signals, status, pendingCount, clear, reconnect],
  )
}
