import { readonly, ref } from 'vue'

import { ApiError, apiGet, apiPost } from '../services/api'

export type WatchlistItem = {
  code: string
  name: string
  type?: string
  role?: string
  bars?: number
  last_date?: string | null
  close?: number | null
  pct_chg?: number | null
  state?: string | null
  state_days?: number | null
  trend_score?: number | null
  opportunity_score?: number | null
}

export type Coverage = {
  instruments?: number
  stocks?: number
  etfs?: number
  indices?: number
  latest_trade_date?: string | null
  [key: string]: unknown
}

export type SearchItem = {
  code: string
  name: string
  type?: string
}

export type ActionResult = {
  ok: boolean
  message?: string
  code?: string
  kind?: string
}

const items = ref<WatchlistItem[]>([])
const coverage = ref<Coverage>({})
const selectedCode = ref('')
const loading = ref(false)
const error = ref('')
const searchResults = ref<SearchItem[]>([])

async function refreshWatchlist(): Promise<WatchlistItem[]> {
  loading.value = true
  error.value = ''
  try {
    const payload = await apiGet<{ items: WatchlistItem[]; coverage: Coverage }>('/watchlist')
    items.value = payload.items || []
    coverage.value = payload.coverage || {}
    if (!items.value.some((item) => item.code === selectedCode.value)) {
      selectedCode.value = items.value[0]?.code || ''
    }
    return items.value
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '观察池读取失败'
    throw cause
  } finally {
    loading.value = false
  }
}

async function search(query: string): Promise<SearchItem[]> {
  const text = query.trim()
  if (!text) {
    searchResults.value = []
    return []
  }
  const payload = await apiGet<{ items: SearchItem[] }>(`/search?q=${encodeURIComponent(text)}`)
  searchResults.value = payload.items || []
  return searchResults.value
}

async function addInstrument(code: string, kind?: string): Promise<ActionResult> {
  const result = await apiPost<ActionResult>('/watchlist', { action: 'add', code, kind })
  if (!result.ok) throw new Error(result.message || '添加失败')
  await refreshWatchlist()
  return result
}

async function removeInstrument(code: string): Promise<ActionResult> {
  const result = await apiPost<ActionResult>('/watchlist', { action: 'remove', code })
  if (!result.ok) throw new Error(result.message || '移除失败')
  await refreshWatchlist()
  return result
}

function setSelectedCode(code: string) {
  selectedCode.value = code
}

function resetForTests() {
  items.value = []
  coverage.value = {}
  selectedCode.value = ''
  loading.value = false
  error.value = ''
  searchResults.value = []
}

export function useAppStore() {
  return {
    items: readonly(items),
    coverage: readonly(coverage),
    selectedCode,
    loading: readonly(loading),
    error: readonly(error),
    searchResults: readonly(searchResults),
    refreshWatchlist,
    search,
    addInstrument,
    removeInstrument,
    setSelectedCode,
    resetForTests,
  }
}
