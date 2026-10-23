import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import FactorsView from './FactorsView.vue'

function json(payload: unknown) {
  return new Response(JSON.stringify(payload), { status: 200 })
}

describe('FactorsView', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('renders factor status, IC and shadow progress', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: string | URL | Request) => {
        const url = String(input)
        if (url.includes('/api/factors')) {
          return Promise.resolve(
            json({
              ok: true,
              as_of: '2026-10-22',
              by_status: { active: 1, shadow: 1 },
              shadow_days: 20,
              duplicate_corr: 0.7,
              violations: [],
              items: [
                {
                  factor_id: 'trend',
                  name: '趋势',
                  layer: 'trend',
                  category: 'momentum',
                  weight: 0.18,
                  status: 'active',
                  days: 120,
                  last_date: '2026-10-22',
                  ic5: 0.032,
                  ic20: 0.061,
                  ic20_partial: 0.028,
                  max_corr: 0.42,
                  max_corr_with: 'ma20',
                  verdict: '独立预测力有效',
                },
                {
                  factor_id: 'volume_new',
                  name: '量能新因子',
                  layer: 'volume',
                  category: 'volume',
                  weight: 0,
                  status: 'shadow',
                  days: 12,
                  ic20: 0.021,
                  max_corr: 0.31,
                  verdict: '样本不足',
                },
              ],
            }),
          )
        }
        if (url.includes('/api/analysis')) {
          return Promise.resolve(json({ ok: true, configured: false, item: null }))
        }
        return Promise.resolve(json({}))
      }),
    )

    const wrapper = mount(FactorsView)
    await flushPromises()

    expect(wrapper.text()).toContain('趋势')
    expect(wrapper.text()).toContain('0.061')
    expect(wrapper.text()).toContain('量能新因子')
    expect(wrapper.text()).toContain('12 / 20')
    wrapper.unmount()
  })
})
