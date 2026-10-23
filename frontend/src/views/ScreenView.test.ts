import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useAppStore } from '../stores/app'
import ScreenView from './ScreenView.vue'

function json(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('ScreenView', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    useAppStore().resetForTests()
  })

  it('renders candidates, pool members and net outcomes', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        json({
          ok: true,
          days: 20,
          min_hits: 2,
          min_avg_amount: 100000000,
          cost: 0.302,
          screen_stale: false,
          exit_streak: 3,
          asset_mix: { score: 0.4, stance: '中性', weights: { stock: 0.5 } },
          candidates: [
            {
              code: 'SH600000',
              name: '浦发银行',
              kind_label: '个股',
              hits: 3,
              days: 2,
              criteria: ['放量突破'],
              avg_amount_60d: 900000000,
              liquid: true,
              excess_20d: 2.4,
              outcome_n: 12,
              cap: 0.08,
              stop_pct: 5.2,
              risk_reward: 2.1,
            },
          ],
          members: [
            {
              code: 'SZ000001',
              name: '平安银行',
              type: 'stock',
              hits: 4,
              criteria: ['趋势回踩'],
              state: 'up',
              miss_streak: 0,
              last_date: '2026-10-22',
              avg_amount_60d: 1200000000,
              cap: 0.1,
            },
          ],
          outcomes: [
            {
              criterion: '放量突破',
              n: 75,
              done5: 60,
              done20: 40,
              win5_net: 35,
              win20_net: 22,
              avg5_net: 0.8,
              avg20: 2.1,
              avg20_net: 1.8,
            },
          ],
          pattern_stats: { patterns: [] },
        }),
      ),
    )

    const wrapper = mount(ScreenView)
    await flushPromises()

    expect(wrapper.text()).toContain('浦发银行')
    expect(wrapper.text()).toContain('平安银行')
    expect(wrapper.text()).toContain('放量突破')
    expect(wrapper.text()).toContain('1.80%')
    wrapper.unmount()
  })
})
