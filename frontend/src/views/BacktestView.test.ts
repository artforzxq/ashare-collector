import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import BacktestView from './BacktestView.vue'

function json(payload: unknown) {
  return new Response(JSON.stringify(payload), { status: 200 })
}

describe('BacktestView', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('renders parameter combinations and turnover research', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: string | URL | Request) => {
        const url = String(input)
        if (url.includes('/api/backtest')) {
          return Promise.resolve(
            json({
              ok: true,
              saved_at: '2026-10-22 20:00',
              total: 2,
              code_count: 500,
              years: 4.2,
              cost: 0.302,
              limit_check: true,
              current: { enter_up: 0.7, confirm_days: 2, min_state_days: 3 },
              sample: { mode: 'market' },
              items: [
                {
                  label: 'enter_up=0.7',
                  params: { enter_up: 0.7, confirm_days: 2, min_state_days: 3 },
                  signals: 320,
                  win20: 55.2,
                  avg20: 2.3,
                  base20: 1.2,
                  excess20: 1.1,
                  mdd20: -8.4,
                  freq: 2.4,
                },
              ],
              turnover: {
                ok: true,
                codes: 500,
                years: 4.2,
                covered: 1200,
                buckets: [
                  {
                    bucket: '3 倍以上',
                    n20: 180,
                    days20: 44,
                    excess_by_day20: -0.8,
                    t20: -2.2,
                    excess20: -0.9,
                    win20: 42.1,
                  },
                ],
              },
            }),
          )
        }
        if (url.includes('/api/analysis')) {
          return Promise.resolve(json({ ok: true, configured: false, item: null }))
        }
        return Promise.resolve(json({}))
      }),
    )

    const wrapper = mount(BacktestView)
    await flushPromises()

    expect(wrapper.text()).toContain('enter_up=0.7')
    expect(wrapper.text()).toContain('+1.10%')
    expect(wrapper.text()).toContain('3 倍以上')
    wrapper.unmount()
  })
})
