import { describe, expect, it } from 'vitest'

import { chartOptionForCandles, chartOptionForIntraday } from './options'

describe('chart options', () => {
  const bars = [
    {
      trade_date: '2026-10-20',
      open: 10,
      high: 10.8,
      low: 9.9,
      close: 10.6,
      volume: 1000,
      ma20: 10.2,
      ma60: null,
      ma120: null,
      pct_chg: 6,
      candle: null,
    },
    {
      trade_date: '2026-10-21',
      open: 10.6,
      high: 11,
      low: 10.3,
      close: 10.9,
      volume: 1200,
      ma20: 10.3,
      ma60: null,
      ma120: null,
      pct_chg: 2.83,
      candle: { policy_confirm: true },
    },
  ]

  it('builds candlestick and volume series', () => {
    const option = chartOptionForCandles(
      {
        code: 'SH600000',
        bars,
        levels: [],
        alerts: [],
      },
      { movingAverages: true, bands: true, alerts: true, patterns: true, volume: true },
      {
        up: '#f00',
        down: '#0a0',
        grid: '#eee',
        axis: '#999',
        text: '#333',
        ma20: '#fa0',
        ma60: '#09f',
        ma120: '#90f',
        support: 'rgba(0,0,255,.1)',
        resistance: 'rgba(255,0,0,.1)',
        range: 'rgba(100,100,100,.1)',
      },
    )

    const series = option.series as Array<{ type: string }>
    expect(series).toHaveLength(5)
    expect(series[0]?.type).toBe('candlestick')
    expect(series[4]?.type).toBe('bar')
  })

  it('builds intraday price and average lines', () => {
    const option = chartOptionForIntraday(
      {
        points: [
          { t: '09:30', p: 10, avg: 10, v: 100 },
          { t: '09:31', p: 10.1, avg: 10.05, v: 200 },
        ],
        prev_close: 9.9,
      },
      { up: '#f00', down: '#0a0', grid: '#eee', axis: '#999', text: '#333' },
    )

    const series = option.series as Array<{ type: string }>
    expect(series).toHaveLength(3)
    expect(series[0]?.type).toBe('line')
    expect(series[2]?.type).toBe('bar')
  })
})
