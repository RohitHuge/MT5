import { useEffect, useRef, useState } from 'react'
import { createChart, LineStyle, type IChartApi, type ISeriesApi, type Time } from 'lightweight-charts'
import { getJson } from '../../lib/api'
import { CHART_COLORS } from '../../lib/chartColors'
import { errorMessage } from '../../lib/errors'
import { EmptyState, ErrorState, LoadingState } from '../StateViews'
import type { Candle } from '../../types/api'

/** Chart overlay rendered on top of the candles. */
export type Overlay =
  | { kind: 'ema'; period: number; color: string; data: { time: number; value: number }[] }
  | { kind: 'zones'; color: string; zones: { timeStart: number; high: number; low: number }[] }
  | { kind: 'hline'; color: string; levels: number[] }
  | { kind: 'markers'; markers: SeriesMarker[] }

/** Marker shape accepted by lightweight-charts v4's `candles.setMarkers()`. */
export interface SeriesMarker {
  time: number
  position: 'aboveBar' | 'belowBar' | 'inBar'
  color: string
  shape: 'circle' | 'square' | 'arrowUp' | 'arrowDown'
  text?: string
}

export interface CandlePoint {
  time: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

interface CandleChartProps {
  symbol: string
  timeframe: string
  height?: number
  overlays?: Overlay[]
  showVolume?: boolean
}

const TICK_MS = 500
const BACKOFF_MAX_MS = 30_000

function toPoints(rows: Candle[]): CandlePoint[] {
  return rows
    .map((row) => {
      const ms = Date.parse(row.timestamp)
      if (Number.isNaN(ms)) return null
      return {
        time: Math.floor(ms / 1000),
        open: Number(row.open),
        high: Number(row.high),
        low: Number(row.low),
        close: Number(row.close),
        volume: Number(row.volume ?? 0),
      }
    })
    .filter((p): p is CandlePoint => p !== null)
    .sort((a, b) => a.time - b.time)
}

/**
 * Candlestick chart with live updates and strategy overlays.
 *
 * Candles come from `GET /api/candles` (initial history) and `WS /ws/candles`
 * (closed bars). The 5s poll re-reads the newest bar so a bar that closed
 * between socket messages is picked up.
 *
 * Note: the bridge exposes no tick/quote endpoint, so there is no sub-bar
 * price — the last bar updates only when a new bar closes.
 */
export function CandleChart({
  symbol,
  timeframe,
  height = 420,
  overlays = [],
  showVolume = true,
}: CandleChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const overlaySeriesRef = useRef<ISeriesApi<'Line'>[]>([])

  const lastBarRef = useRef<CandlePoint | null>(null)

  const [status, setStatus] = useState<'loading' | 'ready' | 'empty' | 'error'>('loading')
  const [error, setError] = useState<string | null>(null)

  // ── chart lifecycle ──────────────────────────────────────────────────────
  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const chart = createChart(el, {
      height,
      width: el.clientWidth,
      layout: {
        background: { color: CHART_COLORS.bg },
        textColor: '#e2e8f0',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: CHART_COLORS.border },
        horzLines: { color: CHART_COLORS.border },
      },
      rightPriceScale: { borderColor: CHART_COLORS.border },
      timeScale: { borderColor: CHART_COLORS.border, timeVisible: true },
      crosshair: { mode: 1 },
    })

    const candles = chart.addCandlestickSeries({
      upColor: CHART_COLORS.pos,
      downColor: CHART_COLORS.neg,
      borderUpColor: CHART_COLORS.pos,
      borderDownColor: CHART_COLORS.neg,
      wickUpColor: CHART_COLORS.pos,
      wickDownColor: CHART_COLORS.neg,
    })

    let volume: ISeriesApi<'Histogram'> | null = null
    if (showVolume) {
      volume = chart.addHistogramSeries({
        priceFormat: { type: 'volume' },
        priceScaleId: '',
      })
      volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } })
    }

    chartRef.current = chart
    candleRef.current = candles
    volumeRef.current = volume

    const observer = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect
      if (box) chart.applyOptions({ width: Math.floor(box.width) })
    })
    observer.observe(el)

    return () => {
      observer.disconnect()
      overlaySeriesRef.current = []
      chart.remove()
      chartRef.current = null
      candleRef.current = null
      volumeRef.current = null
    }
  }, [height, showVolume])

  // ── data: initial history + websocket + poll ─────────────────────────────
  useEffect(() => {
    const candles = candleRef.current
    const volume = volumeRef.current
    if (!candles) return

    let cancelled = false
    let socket: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let pollTimer: ReturnType<typeof setInterval> | null = null
    let backoff = 2_000
    let lastTime: number | null = null

    setStatus('loading')
    setError(null)

    const applyRows = (points: CandlePoint[]) => {
      if (points.length === 0) {
        if (lastTime === null) setStatus('empty')
        return
      }
      candles.setData(
        points.map((p) => ({
          time: p.time as Time,
          open: p.open,
          high: p.high,
          low: p.low,
          close: p.close,
        })),
      )
      if (volume) {
        volume.setData(
          points.map((p) => ({
            time: p.time as Time,
            value: p.volume,
            color: p.close >= p.open ? `${CHART_COLORS.pos}55` : `${CHART_COLORS.neg}55`,
          })),
        )
      }
      lastTime = points[points.length - 1].time
      setStatus('ready')
    }

    const applyOne = (point: CandlePoint) => {
      candles.update({
        time: point.time as Time,
        open: point.open,
        high: point.high,
        low: point.low,
        close: point.close,
      })
      if (volume) {
        volume.update({
          time: point.time as Time,
          value: point.volume,
          color: point.close >= point.open ? `${CHART_COLORS.pos}55` : `${CHART_COLORS.neg}55`,
        })
      }
      lastTime = point.time
      lastBarRef.current = point
      setStatus('ready')
    }

    const loadHistory = async () => {
      try {
        const rows = await getJson<Candle[]>(
          `/api/candles?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}&count=200`,
        )
        if (cancelled) return
        applyRows(toPoints(Array.isArray(rows) ? rows : []))
      } catch (err) {
        if (cancelled) return
        setError(errorMessage(err, 'Could not load candles'))
        setStatus('error')
      }
    }

    // Poll /api/tick every 500ms to animate the forming bar's close price.
    // The WebSocket handles closed bars; this only updates the last bar's close
    // so the chart moves in real time between bar closes.
    const startPolling = () => {
      pollTimer = setInterval(async () => {
        try {
          const tick = await getJson<{ bid: number; ask: number; time: string }>(
            `/api/tick?symbol=${encodeURIComponent(symbol)}`,
          )
          if (cancelled || !tick || !lastBarRef.current) return
          const last = lastBarRef.current
          applyOne({
            ...last,
            close: tick.ask,
            high: Math.max(last.high, tick.ask),
            low: Math.min(last.low, tick.ask),
          })
        } catch {
          // Tick polling is best-effort; the chart still works from closed bars alone.
        }
      }, TICK_MS)
    }

    const connect = () => {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const url = `${proto}//${window.location.host}/ws/candles?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}`
      socket = new WebSocket(url)

      socket.onopen = () => {
        backoff = 2_000
      }

      socket.onmessage = (event) => {
        if (cancelled) return
        try {
          const data = JSON.parse(event.data as string)
          if (!data || data.type === 'ping' || data.type === 'pong') return
          const points = toPoints([data as Candle])
          if (points.length) applyOne(points[0])
        } catch {
          // Ignore malformed frames rather than breaking the feed.
        }
      }

      socket.onclose = () => {
        if (cancelled) return
        reconnectTimer = setTimeout(connect, backoff)
        backoff = Math.min(backoff * 2, BACKOFF_MAX_MS)
      }

      socket.onerror = () => {
        socket?.close()
      }
    }

    void loadHistory()
    connect()
    startPolling()

    return () => {
      cancelled = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      if (pollTimer) clearInterval(pollTimer)
      socket?.close()
    }
  }, [symbol, timeframe])

  // ── overlays ─────────────────────────────────────────────────────────────
  useEffect(() => {
    const candles = candleRef.current
    const chart = chartRef.current
    if (!candles || !chart) return

    // Clear previous overlay series and markers.
    for (const series of overlaySeriesRef.current) {
      try {
        chart.removeSeries(series)
      } catch {
        // Already removed when the chart was recreated.
      }
    }
    overlaySeriesRef.current = []
    candles.setMarkers([])

    for (const overlay of overlays) {
      if (overlay.kind === 'ema') {
        const series = chart.addLineSeries({
          color: overlay.color,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          title: `EMA${overlay.period}`,
        })
        series.setData(
          overlay.data.map((p) => ({ time: p.time as Time, value: p.value })),
        )
        overlaySeriesRef.current.push(series)
      }

      if (overlay.kind === 'zones') {
        // lightweight-charts v4 has no filled rectangle series, so each zone is
        // drawn as its upper and lower boundary — reads as a band on the chart.
        const top = chart.addLineSeries({
          color: overlay.color,
          lineWidth: 1,
          lineStyle: LineStyle.Solid,
          priceLineVisible: false,
          lastValueVisible: false,
        })
        const bottom = chart.addLineSeries({
          color: overlay.color,
          lineWidth: 1,
          lineStyle: LineStyle.Solid,
          priceLineVisible: false,
          lastValueVisible: false,
        })
        top.setData(
          overlay.zones.map((z) => ({ time: z.timeStart as Time, value: z.high })),
        )
        bottom.setData(
          overlay.zones.map((z) => ({ time: z.timeStart as Time, value: z.low })),
        )
        overlaySeriesRef.current.push(top, bottom)
      }

      if (overlay.kind === 'hline') {
        // One dashed price line per level — clearer than a flat series.
        for (const level of overlay.levels) {
          candles.createPriceLine({
            price: level,
            color: overlay.color,
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            axisLabelVisible: true,
            title: '',
          })
        }
      }

      if (overlay.kind === 'markers') {
        candles.setMarkers(
          overlay.markers.map((marker) => ({ ...marker, time: marker.time as Time })),
        )
      }
    }
  }, [overlays])

  // ── render guards ────────────────────────────────────────────────────────
  return (
    <div className="relative">
      <div ref={containerRef} className="w-full" style={{ height }} />
      {status === 'loading' && (
        <div className="absolute inset-0 flex items-center justify-center">
          <LoadingState label="Loading chart…" rows={2} />
        </div>
      )}
      {status === 'empty' && (
        <div className="absolute inset-0 flex items-center justify-center">
          <EmptyState
            title="No candles available"
            hint="The MT5 bridge is offline, or the symbol has no history yet."
            className="max-w-sm"
          />
        </div>
      )}
      {status === 'error' && (
        <div className="absolute inset-0 flex items-center justify-center">
          <ErrorState message={error ?? 'Could not load candles'} className="max-w-sm" />
        </div>
      )}
    </div>
  )
}
