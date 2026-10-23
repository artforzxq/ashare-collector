import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import JobRunner from './JobRunner.vue'

function json(payload: unknown) {
  return new Response(JSON.stringify(payload), { status: 200 })
}

describe('JobRunner', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('submits a selected job through the shared jobs endpoint', async () => {
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/api/jobs') && init?.method === 'POST') {
        return Promise.resolve(json({ ok: true }))
      }
      return Promise.resolve(json({
        running: null,
        recent: [],
        reference_day: '2026-10-23',
        server_now: '2026-10-23 16:30:00',
        summary: { overdue: 1, pending: 0, done: 1, blocked: 1, failed: 0, manual: 6 },
        plan: [
          { key: 'sync', title: '同步全市场', cadence: 'manual', status: 'manual',
            last_success: null, data_asof: '2026-10-23' },
          { key: 'daily', title: '每日任务', cadence: 'trading_day', after: '15:30',
            status: 'done', expected_for: '2026-10-23 15:30:00',
            last_success: { finished_at: '2026-10-23 15:40:00' }, data_asof: '2026-10-23' },
          { key: 'screen', title: '全市场筛选', cadence: 'trading_day', after: '16:00',
            status: 'overdue', expected_for: '2026-10-23 16:00:00',
            depends_on: ['daily'], last_success: null, data_asof: '2026-10-16' },
          { key: 'push', title: '推送简报', cadence: 'trading_day', after: '15:40',
            status: 'blocked', expected_for: '2026-10-23 15:40:00',
            depends_on: ['daily'], last_success: null, data_asof: null },
        ],
        schedule: [
          { date: '2026-10-23', key: 'screen', title: '全市场筛选',
            expected_for: '2026-10-23 16:00:00', status: 'overdue',
            finished_at: null, data_asof: '2026-10-16' },
        ],
      }))
    })
    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(JobRunner)
    await flushPromises()
    expect(wrapper.text()).toContain('应执行未执行')
    expect(wrapper.text()).toContain('依赖未完成')
    expect(wrapper.text()).toContain('近期计划与结果')
    expect(wrapper.text()).toContain('2026-10-16')
    const button = wrapper
      .findAll('button')
      .find((item) => item.text().includes('同步全市场'))
    expect(button).toBeTruthy()
    await button!.trigger('click')
    await flushPromises()

    const post = fetchMock.mock.calls.find((call) => (call[1] as RequestInit | undefined)?.method === 'POST')
    expect(post?.[0]).toBe('/api/jobs')
    expect(JSON.parse(String((post?.[1] as RequestInit).body))).toMatchObject({ job: 'sync' })
    wrapper.unmount()
  })
})
