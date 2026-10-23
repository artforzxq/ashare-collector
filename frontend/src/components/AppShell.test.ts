import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { router } from '../router'
import { useAppStore } from '../stores/app'
import AppShell from './AppShell.vue'

describe('AppShell', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    useAppStore().resetForTests()
  })

  it('renders the shared watchlist and search input', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            items: [
              { code: 'SH600000', name: '浦发银行', state: 'up', pct_chg: 1.2 },
              { code: 'SZ000001', name: '平安银行', state: 'range', pct_chg: -0.3 },
            ],
            coverage: { instruments: 5000 },
          }),
          { status: 200 },
        ),
      ),
    )

    await router.push('/overview')
    await router.isReady()
    await useAppStore().refreshWatchlist()
    const wrapper = mount(AppShell, {
      global: { plugins: [router] },
      attachTo: document.body,
    })
    await flushPromises()

    expect(wrapper.text()).toContain('浦发银行')
    expect(wrapper.text()).toContain('观察池')
    expect(wrapper.find('input[placeholder*="搜索"]').exists()).toBe(true)
    wrapper.unmount()
  })
})
