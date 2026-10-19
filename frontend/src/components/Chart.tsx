import { useEffect, useRef } from 'react'
import { createChart, IChartApi, ISeriesApi, SeriesMarker, Time } from 'lightweight-charts'

export type Candle = { time: number; open: number; high: number; low: number; close: number; volume?: number }
export type Marker = { time: number; position: 'aboveBar' | 'belowBar'; color: string; shape: 'arrowUp' | 'arrowDown' | 'circle'; text?: string }

export default function Chart({ candles, markers = [], line, height = 420, fit = true }: { candles: Candle[]; markers?: Marker[]; line?: { time: number; value: number }[]; height?: number; fit?: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const csRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const vsRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const lnRef = useRef<ISeriesApi<'Line'> | null>(null)

  useEffect(() => {
    if (!ref.current) return
    const chart = createChart(ref.current, {
      height, layout: { background: { color: '#fff' }, textColor: '#333' },
      grid: { vertLines: { color: '#eee' }, horzLines: { color: '#eee' } },
      timeScale: { timeVisible: true, secondsVisible: false }, localization: { locale: 'zh-CN' },
    })
    const cs = chart.addCandlestickSeries({ upColor: '#26a69a', downColor: '#ef5350', borderVisible: false, wickUpColor: '#26a69a', wickDownColor: '#ef5350' })
    const vs = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: '' })
    vs.priceScale().applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } })
    chartRef.current = chart; csRef.current = cs; vsRef.current = vs; lnRef.current = null
    const ro = new ResizeObserver(() => chart.applyOptions({ width: ref.current!.clientWidth }))
    ro.observe(ref.current)
    return () => { ro.disconnect(); chart.remove() }
  }, [height])

  useEffect(() => {
    const cs = csRef.current, vs = vsRef.current, chart = chartRef.current
    if (!cs || !vs || !chart) return
    cs.setData(candles.map(c => ({ time: c.time as Time, open: c.open, high: c.high, low: c.low, close: c.close })))
    vs.setData(candles.map(c => ({ time: c.time as Time, value: c.volume ?? 0, color: c.close >= c.open ? 'rgba(38,166,154,0.35)' : 'rgba(239,83,80,0.35)' })))
    cs.setMarkers([...markers].sort((a, b) => a.time - b.time).map(m => ({ ...m, time: m.time as Time })) as SeriesMarker<Time>[])
    if (line) {
      if (!lnRef.current) lnRef.current = chart.addLineSeries({ color: '#1565c0', lineWidth: 2 })
      lnRef.current.setData(line.map(p => ({ time: p.time as Time, value: p.value })))
    }
    if (fit) chart.timeScale().fitContent(); else chart.timeScale().scrollToRealTime()
  }, [candles, markers, line, fit])

  return <div ref={ref} className="chart" style={{ height }} />
}
