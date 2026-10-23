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
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(json({
        ok: true,
        days: 20,
        min_hits: 2,
        min_avg_amount: 100000000,
        cost: 0.302,
        screen_stale: false,
        exit_streak: 3,
        trade_date: '2026-10-16',
        asset_mix: { score: 0.4, stance: '中性', available: true, weights: { stock: 0.5 } },
        kind_counts: { all: 1, stock: 1, etf: 0, broad_etf: 0, sector_etf: 0, index: 0 },
        market: {
          trade_date: '2026-10-16',
          exact: true,
          up_ratio: 0.45,
          limit_up_count: 40,
          limit_down_count: 12,
          broken_limit_count: 15,
          max_boards: 4,
          total_amount: 2e12,
          sentiment_stage: '发酵',
          sentiment_note: '测试情绪',
        },
        candidates: [
          {
            code: 'SH600000',
            name: '浦发银行',
            kind: 'stock',
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
            why: ['反复命中：3 次 / 2 个筛选日', '流动性：9.00 亿，达标'],
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
      })),
    )
    vi.stubGlobal(
      'fetch',
      fetchMock,
    )

    const wrapper = mount(ScreenView)
    await flushPromises()

    expect(wrapper.text()).toContain('浦发银行')
    expect(wrapper.text()).toContain('平安银行')
    expect(wrapper.text()).toContain('放量突破')
    expect(wrapper.text()).toContain('1.80%')
    expect(wrapper.text()).toContain('筛选快照与市场姿态')
    expect(wrapper.text()).toContain('市场层已评分')

    const stockButton = wrapper.findAll('button').find((button) => button.text().includes('个股'))
    await stockButton?.trigger('click')
    await flushPromises()
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('kind=stock'))).toBe(true)

    const whyButton = wrapper.findAll('button').find((button) => button.text() === '为什么')
    await whyButton?.trigger('click')
    expect(wrapper.text()).toContain('为什么它在榜上')
    expect(wrapper.text()).toContain('反复命中：3 次')
    wrapper.unmount()
  })
})
