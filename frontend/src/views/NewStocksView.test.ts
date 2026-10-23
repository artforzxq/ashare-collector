import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import NewStocksView from './NewStocksView.vue'

function json(payload: unknown) {
  return new Response(JSON.stringify(payload), { status: 200 })
}

describe('NewStocksView', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('renders new and recent listings with listing-stage evidence', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        json({
          ok: true,
          as_of: '2026-10-22',
          counts: { new: 1, recent: 1 },
          new_days: 30,
          recent_days: 250,
          threshold: 100000000,
          new: [
            {
              code: 'SH688001',
              name: '新股甲',
              listed_date: '2026-10-10',
              trading_days: 8,
              boards_from_start: 5,
              since_list_pct: 88.2,
              last_close: 22.1,
              estimated: false,
            },
          ],
          recent: [
            {
              code: 'SZ301001',
              name: '次新乙',
              listed_date: '2026-04-10',
              trading_days: 120,
              boards_from_start: 3,
              since_list_pct: 20.4,
              last_close: 15.2,
              estimated: false,
            },
          ],
        }),
      ),
    )

    const wrapper = mount(NewStocksView)
    await flushPromises()

    expect(wrapper.text()).toContain('新股甲')
    expect(wrapper.text()).toContain('次新乙')
    expect(wrapper.text()).toContain('5 连板')
    expect(wrapper.text()).toContain('88.20%')
    wrapper.unmount()
  })
})
