import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useAppStore } from '../stores/app'
import InstrumentView from './InstrumentView.vue'

function json(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('InstrumentView', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    useAppStore().resetForTests()
  })

  it('renders the latest decision metrics for the selected instrument', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: string | URL | Request) => {
        const url = String(input)
        if (url.includes('/api/kline')) {
          return Promise.resolve(
            json({
              code: 'SH600000',
              name: '浦发银行',
              type: 'stock',
              bars: [
                {
                  trade_date: '2026-10-22',
                  open: 10.2,
                  high: 10.8,
                  low: 10.1,
                  close: 10.6,
                  volume: 1000,
                  pct_chg: 2.1,
                  ma20: 10.2,
                  ma60: 9.9,
                  ma120: 9.4,
                  trend_score: 72,
                  opportunity_score: 0.61,
                  state: 'up',
                  state_days: 5,
                  vol_ratio_20: 1.4,
                  position_cap: 0.12,
                  stop_level: 9.5,
                  risk_reward: 2.2,
                  candle_pattern: '放量长阳',
                  risk_note: '趋势向上，关键带支撑有效',
                },
              ],
              levels: [],
              alerts: [],
              factors: [],
            }),
          )
        }
        if (url.includes('/api/watchlist')) {
          return Promise.resolve(
            json({
              items: [{ code: 'SH600000', name: '浦发银行', state: 'up' }],
              coverage: { instruments: 5000 },
            }),
          )
        }
        if (url.includes('/api/quotes')) {
          return Promise.resolve(
            json({
              trading: false,
              session: '已收盘',
              items: [
                {
                  code: 'SH600000',
                  name: '浦发银行',
                  close: 10.6,
                  pct_chg: 2.1,
                  amount: 100000000,
                },
              ],
            }),
          )
        }
        if (url.includes('/api/meta')) return Promise.resolve(json({ feature_version: 'v1.5' }))
        if (url.includes('/api/analysis')) {
          return Promise.resolve(json({ ok: true, enabled: false, configured: false, item: null }))
        }
        return Promise.resolve(json({}))
      }),
    )
    useAppStore().setSelectedCode('SH600000')

    const wrapper = mount(InstrumentView, {
      global: { stubs: { EChart: true } },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('浦发银行')
    expect(wrapper.text()).toContain('上升趋势')
    expect(wrapper.text()).toContain('12%')
    expect(wrapper.text()).toContain('9.500')
    wrapper.unmount()
  })
})
