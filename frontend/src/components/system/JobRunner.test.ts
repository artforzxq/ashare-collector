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
      return Promise.resolve(json({ running: null, recent: [] }))
    })
    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(JobRunner)
    await flushPromises()
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
