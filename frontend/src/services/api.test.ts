import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiGet } from './api'

describe('apiGet', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('returns parsed JSON for a successful response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )

    await expect(apiGet<{ ok: boolean }>('/health')).resolves.toEqual({ ok: true })
  })

  it('keeps the backend message when a request fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ message: '数据库还没初始化' }), {
          status: 503,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )

    await expect(apiGet('/journal')).rejects.toMatchObject({
      message: '数据库还没初始化',
      status: 503,
      path: '/journal',
    })
  })
})
