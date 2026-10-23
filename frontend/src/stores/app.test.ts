import { afterEach, describe, expect, it, vi } from 'vitest'

import { useAppStore } from './app'

describe('app store', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    useAppStore().resetForTests()
  })

  it('loads the watchlist and selects its first instrument', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            items: [
              { code: 'SH600000', name: '浦发银行', state: 'up' },
              { code: 'SZ000001', name: '平安银行', state: 'range' },
            ],
            coverage: { instruments: 5000 },
          }),
          { status: 200 },
        ),
      ),
    )

    const store = useAppStore()
    await store.refreshWatchlist()

    expect(store.items.value).toHaveLength(2)
    expect(store.selectedCode.value).toBe('SH600000')
    expect(store.coverage.value?.instruments).toBe(5000)
  })

  it('removes stale selected codes after a refresh', async () => {
    const store = useAppStore()
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(
          new Response(
            JSON.stringify({
              items: [{ code: 'SH600000', name: '浦发银行' }],
              coverage: {},
            }),
            { status: 200 },
          ),
        )
        .mockResolvedValueOnce(
          new Response(
            JSON.stringify({ items: [{ code: 'SZ000001', name: '平安银行' }], coverage: {} }),
            { status: 200 },
          ),
        ),
    )

    await store.refreshWatchlist()
    store.setSelectedCode('SH600000')
    await store.refreshWatchlist()

    expect(store.selectedCode.value).toBe('SZ000001')
  })
})
