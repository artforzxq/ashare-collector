import type { EChartsOption } from 'echarts'

export type ChartBar = {
  trade_date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  ma20?: number | null
  ma60?: number | null
  ma120?: number | null
  pct_chg?: number | null
  candle?: Record<string, unknown> | null
}

export type ChartLevel = {
  price_low: number
  price_high: number
  level_type: string
  engine?: string | null
}

export type ChartAlert = {
  trade_date: string
  level: string
  signal_type?: string | null
  title?: string | null
}

export type ChartPalette = {
  up: string
  down: string
  grid: string
  axis: string
  text: string
  ma20?: string
  ma60?: string
  ma120?: string
  support?: string
  resistance?: string
  range?: string
}

export type CandleChartFlags = {
  movingAverages: boolean
  bands: boolean
  alerts: boolean
  patterns: boolean
  volume: boolean
}

export function chartOptionForCandles(
  payload: {
    code?: string
    bars: ChartBar[]
    levels?: ChartLevel[]
    alerts?: ChartAlert[]
  },
  flags: CandleChartFlags,
  palette: ChartPalette,
): EChartsOption {
  const bars = payload.bars || []
  const dates = bars.map((bar) => bar.trade_date)
  const candleData = bars.map((bar) => [bar.open, bar.close, bar.low, bar.high])
  const maSeries = flags.movingAverages
    ? [
        lineSeries('MA20', bars.map((bar) => bar.ma20 ?? null), palette.ma20 || '#f2a13b'),
        lineSeries('MA60', bars.map((bar) => bar.ma60 ?? null), palette.ma60 || '#3b82f6'),
        lineSeries('MA120', bars.map((bar) => bar.ma120 ?? null), palette.ma120 || '#8b5cf6'),
      ]
    : []
  const volumeSeries = flags.volume ? [volumeBarSeries(bars, palette)] : []

  const markLineData = flags.bands
    ? (payload.levels || []).flatMap((level) => {
        const color = level.level_type === 'support'
          ? palette.support || palette.up
          : level.level_type === 'range'
            ? palette.range || palette.axis
            : palette.resistance || palette.down
        return [level.price_low, level.price_high].map((yAxis) => ({
          yAxis,
          lineStyle: { color, type: 'dashed' as const, width: 1 },
          label: { show: false },
        }))
      })
    : []

  const markPointData = [
    ...(flags.alerts ? alertPoints(payload.alerts || [], bars, palette) : []),
    ...(flags.patterns ? patternPoints(bars, palette) : []),
  ]

  const candleSeries = {
    name: payload.code || '日 K',
    type: 'candlestick' as const,
    data: candleData,
    itemStyle: {
      color: palette.up,
      color0: palette.down,
      borderColor: palette.up,
      borderColor0: palette.down,
    },
    markLine: { silent: true, symbol: 'none', data: markLineData },
    markPoint: {
      symbolSize: 34,
      label: { color: '#fff', fontSize: 9 },
      data: markPointData,
    },
  }

  return {
    animation: false,
    backgroundColor: 'transparent',
    grid: flags.volume
      ? [
          { left: 8, right: 62, top: 18, height: '63%' },
          { left: 8, right: 62, top: '78%', height: '16%' },
        ]
      : [{ left: 8, right: 62, top: 18, bottom: 32 }],
    xAxis: flags.volume
      ? [
          {
            type: 'category',
            data: dates,
            boundaryGap: true,
            axisLine: { lineStyle: { color: palette.grid } },
            axisLabel: { show: false },
            axisTick: { show: false },
          },
          {
            type: 'category',
            gridIndex: 1,
            data: dates,
            axisLine: { lineStyle: { color: palette.grid } },
            axisLabel: { color: palette.text, fontSize: 9 },
            axisTick: { show: false },
          },
        ]
      : [
          {
            type: 'category',
            data: dates,
            axisLine: { lineStyle: { color: palette.grid } },
            axisLabel: { color: palette.text, fontSize: 9 },
            axisTick: { show: false },
          },
        ],
    yAxis: flags.volume
      ? [
          {
            scale: true,
            splitLine: { lineStyle: { color: palette.grid } },
            axisLabel: { color: palette.text, fontSize: 9 },
          },
          {
            gridIndex: 1,
            scale: true,
            splitLine: { show: false },
            axisLabel: { color: palette.text, fontSize: 8 },
          },
        ]
      : [
          {
            scale: true,
            splitLine: { lineStyle: { color: palette.grid } },
            axisLabel: { color: palette.text, fontSize: 9 },
          },
        ],
    dataZoom: [
      { type: 'inside', xAxisIndex: flags.volume ? [0, 1] : [0] },
      {
        type: 'slider',
        show: false,
        xAxisIndex: flags.volume ? [0, 1] : [0],
      },
    ],
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      borderColor: palette.grid,
      backgroundColor: 'rgba(15,23,42,.92)',
      textStyle: { color: '#fff', fontSize: 11 },
    },
    legend: { show: false },
    series: [candleSeries, ...maSeries, ...volumeSeries],
  }
}

export function chartOptionForIntraday(
  payload: {
    points?: Array<{ t: string; p: number; avg: number; v: number }>
    prev_close?: number | null
  },
  palette: ChartPalette,
): EChartsOption {
  const points = payload.points || []
  const dates = points.map((point) => point.t)
  const time = points.map((point) => point.p)
  const average = points.map((point) => point.avg)
  const volume = points.map((point) => point.v)

  return {
    animation: false,
    backgroundColor: 'transparent',
    grid: [
      { left: 8, right: 62, top: 16, height: '68%' },
      { left: 8, right: 62, top: '82%', height: '13%' },
    ],
    tooltip: {
      trigger: 'axis',
      borderColor: palette.grid,
      backgroundColor: 'rgba(15,23,42,.92)',
      textStyle: { color: '#fff', fontSize: 11 },
    },
    xAxis: [
      {
        type: 'category',
        data: dates,
        boundaryGap: false,
        axisLine: { lineStyle: { color: palette.grid } },
        axisLabel: { show: false },
        axisTick: { show: false },
      },
      {
        type: 'category',
        gridIndex: 1,
        data: dates,
        boundaryGap: true,
        axisLine: { lineStyle: { color: palette.grid } },
        axisLabel: { color: palette.text, fontSize: 9 },
        axisTick: { show: false },
      },
    ],
    yAxis: [
      {
        scale: true,
        splitLine: { lineStyle: { color: palette.grid } },
        axisLabel: { color: palette.text, fontSize: 9 },
      },
      {
        gridIndex: 1,
        scale: true,
        splitLine: { show: false },
        axisLabel: { color: palette.text, fontSize: 8 },
      },
    ],
    series: [
      {
        name: '价格',
        type: 'line',
        data: time,
        showSymbol: false,
        lineStyle: { width: 1.5, color: palette.up },
        areaStyle: { opacity: 0.04, color: palette.up },
        markLine: payload.prev_close
          ? {
              silent: true,
              symbol: 'none',
              data: [{ yAxis: payload.prev_close }],
              lineStyle: { color: palette.axis, type: 'dashed' as const, width: 1 },
              label: { show: false },
            }
          : undefined,
      },
      {
        name: '均价',
        type: 'line',
        data: average,
        showSymbol: false,
        lineStyle: { width: 1.2, color: palette.ma20 || '#f2a13b' },
      },
      {
        name: '成交量',
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: volume,
        itemStyle: { color: palette.axis, opacity: 0.45 },
      },
    ],
  }
}

function lineSeries(name: string, data: Array<number | null>, color: string) {
  return {
    name,
    type: 'line' as const,
    data,
    showSymbol: false,
    connectNulls: false,
    smooth: false,
    lineStyle: { width: 1, color },
    emphasis: { disabled: true },
  }
}

function volumeBarSeries(bars: ChartBar[], palette: ChartPalette) {
  return {
    name: '成交量',
    type: 'bar' as const,
    xAxisIndex: 1,
    yAxisIndex: 1,
    data: bars.map((bar) => ({
      value: bar.volume,
      itemStyle: { color: bar.close >= bar.open ? palette.up : palette.down, opacity: 0.55 },
    })),
  }
}

function alertPoints(alerts: ChartAlert[], bars: ChartBar[], palette: ChartPalette) {
  const byDate = new Map(alerts.map((alert) => [alert.trade_date, alert]))
  return bars.flatMap((bar) => {
    const alert = byDate.get(bar.trade_date)
    if (!alert) return []
    return [
      {
        name: alert.signal_type || alert.level,
        coord: [bar.trade_date, bar.low],
        value: alert.level,
        symbol: 'circle',
        symbolSize: 17,
        itemStyle: {
          color: alert.level === 'P0' ? palette.up : alert.level === 'P1' ? '#f0a020' : palette.axis,
        },
      },
    ]
  })
}

function patternPoints(bars: ChartBar[], palette: ChartPalette) {
  return bars.flatMap((bar) => {
    const candle = bar.candle
    if (!candle) return []
    const upward = Boolean(candle.up || candle.bullish)
    const downward = Boolean(candle.down || candle.bearish)
    if (!upward && !downward) return []
    return [
      {
        name: candle.label ? String(candle.label) : '形态',
        coord: [bar.trade_date, upward ? bar.low : bar.high],
        value: upward ? '↑' : '↓',
        symbol: 'pin',
        symbolSize: 22,
        itemStyle: { color: upward ? palette.up : palette.down },
      },
    ]
  })
}
