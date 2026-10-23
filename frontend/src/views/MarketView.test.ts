import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import MarketView from './MarketView.vue'

function json(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('MarketView', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('renders breadth, sentiment, mainline and ranks', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: string | URL | Request) => {
        const url = String(input)
        if (url.includes('/api/market')) {
          return Promise.resolve(
            json({
              trade_date: '2026-10-22',
              sample: 5100,
              breadth: {
                up: 3200,
                down: 1600,
                flat: 300,
                limit_up: 42,
                limit_down: 4,
                median_pct: 0.86,
                total_amount: 1230000000000,
              },
              amount_stats: {
                value: 1230000000000,
                coverage: 5100,
                prev_date: '2026-10-21',
                prev_value: 1100000000000,
                prev_coverage: 5096,
                default_window: 20,
                d1: { ratio: 1.118, change_pct: 11.8, comparable: true, label: '偏放' },
                windows: {
                  '1': { days: 1, basis: '前一交易日', ratio: 1.118, change_pct: 11.8,
                         percentile: null, label: '偏放', comparable: true },
                  '5': { days: 5, basis: '5日均量', ratio: 1.08, change_pct: 8.0,
                         percentile: 60, label: '常态', comparable: true },
                  '20': { days: 20, basis: '中位数', ratio: 1.18, change_pct: 18.0,
                          percentile: 72, label: '偏放', comparable: true },
                },
                history: [
                  { trade_date: '2026-10-21', total_amount: 1100000000000, coverage: 5096 },
                  { trade_date: '2026-10-22', total_amount: 1230000000000, coverage: 5100 },
                ],
              },
              distribution: [
                { label: '涨 3~7%', count: 320, direction: 'up' },
                { label: '平盘', count: 30, direction: 'flat' },
                { label: '跌 0~3%', count: 85, direction: 'down' },
              ],
              gainers: [{ code: 'SH600000', name: '浦发银行', pct_chg: 6.2 }],
              losers: [{ code: 'SZ000001', name: '平安银行', pct_chg: -3.4 }],
              sentiment: {
                sentiment_stage: '发酵',
                sentiment_note: '连板高度和上涨占比同步改善',
                max_boards: 5,
                broken_limit_count: 8,
              },
              flow: {
                regime: { label: '存量博弈' },
                broad_etf: { net_inflow: 1200000000 },
                sector_etf: { name: '科技' },
                amount: { pct: 8.2 },
                margin: { change_pct: 0.4 },
                history: [],
              },
              coverage: { instruments: 5400 },
            }),
          )
        }
        if (url.includes('/api/mainline')) {
          return Promise.resolve(
            json({
              ok: true,
              coverage: { coverage: 92.4, industries: 31 },
              industries: [
                {
                  industry: '计算机',
                  ret20: 8.6,
                  share_trend_pp: 1.2,
                  diffusion: 38,
                  top_weeks: 3,
                },
              ],
              mainlines: [{ industry: '计算机' }],
              events: [],
            }),
          )
        }
        if (url.includes('/api/support')) {
          return Promise.resolve(
            json({
              today: {
                date: '2026-10-22',
                triggered: true,
                totals: { inflow: 2200000000, volume_ratio: 1.6 },
                items: [],
              },
            }),
          )
        }
        if (url.includes('/api/analysis')) {
          return Promise.resolve(json({ ok: true, enabled: false, item: null }))
        }
        return Promise.resolve(json({}))
      }),
    )

    const wrapper = mount(MarketView, { global: { stubs: { EChart: true } } })
    await flushPromises()

    expect(wrapper.text()).toContain('3200')
    expect(wrapper.text()).toContain('较昨日')
    expect(wrapper.text()).toContain('P72')
    expect(wrapper.text()).toContain('发酵')
    expect(wrapper.text()).toContain('计算机')
    expect(wrapper.text()).toContain('浦发银行')
    expect(wrapper.text()).toContain('存量博弈')
    expect(wrapper.text()).toContain('绿色 · 下跌')
    expect(wrapper.text()).toContain('零轴居中')
    wrapper.unmount()
  })
})
