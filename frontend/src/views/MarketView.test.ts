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
              distribution: [
                { label: '+3% 以上', count: 320 },
                { label: '-3% 以下', count: 85 },
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
    expect(wrapper.text()).toContain('发酵')
    expect(wrapper.text()).toContain('计算机')
    expect(wrapper.text()).toContain('浦发银行')
    expect(wrapper.text()).toContain('存量博弈')
    wrapper.unmount()
  })
})
