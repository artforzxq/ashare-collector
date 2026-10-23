import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useAppStore } from '../stores/app'
import SystemView from './SystemView.vue'

function json(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('SystemView', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    useAppStore().resetForTests()
  })

  it('shows route totals, method split, groups and page availability', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes('/api/health')) {
          return Promise.resolve(json({
            process_started_at: '2026-10-24 00:00:00',
            code_mtime: '2026-10-24 00:00:00',
            stale: false,
            routes: ['/api/watchlist', '/api/health'],
            route_summary: {
              paths: 20,
              endpoints: 22,
              online: 22,
              by_method: { GET: 20, POST: 2 },
              by_group: [
                { group: '自选与提醒', paths: 7, endpoints: 8 },
                { group: '系统与任务', paths: 4, endpoints: 5 },
              ],
            },
            route_details: [
              { path: '/api/watchlist', methods: ['GET', 'POST'], group: '自选与提醒', online: true },
              { path: '/api/health', methods: ['GET'], group: '系统与任务', online: true },
            ],
            page_routes: [
              { path: '/', label: '旧版单页', online: true },
              { path: '/v2/', label: 'Vue 新版', online: true },
            ],
          }))
        }
        if (url.includes('/api/meta')) {
          return Promise.resolve(json({ feature_version: 'v1.5' }))
        }
        if (url.includes('/api/watchlist')) {
          return Promise.resolve(json({ items: [], coverage: { instruments: 7803 } }))
        }
        return Promise.resolve(json({ running: null, recent: [] }))
      }),
    )

    const wrapper = mount(SystemView)
    await flushPromises()

    expect(wrapper.text()).toContain('22/22')
    expect(wrapper.text()).toContain('20 个路径')
    expect(wrapper.text()).toContain('GET 20')
    expect(wrapper.text()).toContain('POST 2')
    expect(wrapper.text()).toContain('自选与提醒')
    expect(wrapper.text()).toContain('/api/watchlist')
    expect(wrapper.text()).toContain('/v2/')
    expect(wrapper.text()).toContain('在线')
    wrapper.unmount()
  })
})
