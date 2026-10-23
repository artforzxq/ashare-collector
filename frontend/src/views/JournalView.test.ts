import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import JournalView from './JournalView.vue'

function json(payload: unknown) {
  return new Response(JSON.stringify(payload), { status: 200 })
}

describe('JournalView', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('renders open, closed and unlock information', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        json({
          ok: true,
          cost: 0.302,
          open: [
            {
              id: 1,
              code: 'SH600000',
              name: '浦发银行',
              framework: 'short',
              theme: '银行',
              entry_price: 10,
              stop_price: 9.2,
              risk_pct: 0.8,
              position_pct: 8,
              reward_risk: 2.5,
              thesis: '放量突破',
            },
          ],
          closed: [
            {
              id: 2,
              code: 'SZ000001',
              name: '平安银行',
              theme: '银行',
              entry_price: 11,
              exit_price: 11.8,
              pnl_net: 620,
              closed_on: '2026-10-20',
              discipline: 1,
              alpha_beta: 'alpha',
              exit_note: '按计划止盈',
            },
          ],
          risk: {
            open_count: 1,
            total_risk_pct: 0.8,
            limits: { portfolio_risk_pct: 2, single_risk_pct: 1 },
            breaches: [],
          },
          stats: { closed: 1, n: 1, win_rate: 100, expectancy: 620, violations: 0 },
          unlock: { tier: 5, n: 1, next_at: 30, healthy: true },
          guardrails: { flags: [], month_pct: 0.62, consecutive_losses: 0 },
        }),
      ),
    )

    const wrapper = mount(JournalView)
    await flushPromises()

    expect(wrapper.text()).toContain('浦发银行')
    expect(wrapper.text()).toContain('平安银行')
    expect(wrapper.text()).toContain('额度档位')
    expect(wrapper.text()).toContain('按计划止盈')
    wrapper.unmount()
  })
})
