<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Plus, RefreshCw } from '@lucide/vue'
import { useRouter } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import StatusPill from '../components/StatusPill.vue'
import StatusGrid from '../components/research/StatusGrid.vue'
import { ApiError, apiGet, apiPost } from '../services/api'
import { fmt } from '../services/format'
import { useAppStore } from '../stores/app'

type Listing = {
  code: string
  name: string
  board?: string
  listed_date: string
  trading_days: number
  first_close?: number | null
  last_close?: number | null
  since_list_pct?: number | null
  limit_up_days?: number
  boards_from_start?: number
  estimated?: boolean
  last_seen?: string | null
}

type ListingsPayload = {
  ok: boolean
  message?: string
  as_of?: string | null
  counts?: { new: number; recent: number }
  new_days?: number
  recent_days?: number
  threshold?: number
  new?: Listing[]
  recent?: Listing[]
}

const router = useRouter()
const app = useAppStore()
const payload = ref<ListingsPayload | null>(null)
const loading = ref(false)
const refreshing = ref(false)
const message = ref('')
const error = ref('')

const counts = computed(() => payload.value?.counts || { new: 0, recent: 0 })
const statusItems = computed(() => [
  {
    label: '新股',
    value: counts.value.new,
    detail: `上市 ≤ ${payload.value?.new_days || 30} 个交易日`,
    tone: 'warning' as const,
  },
  {
    label: '次新',
    value: counts.value.recent,
    detail: `上市 ≤ ${payload.value?.recent_days || 250} 个交易日`,
  },
  {
    label: '成交额门槛',
    value: payload.value?.threshold ? `${(payload.value.threshold / 1e8).toFixed(2)} 亿` : '不限',
    detail: '与筛选、回测共用',
  },
  {
    label: '数据截至',
    value: payload.value?.as_of || '—',
    detail: '同步未完成时仅供参考',
  },
])

async function loadListings() {
  loading.value = true
  error.value = ''
  try {
    payload.value = await apiGet<ListingsPayload>('/newstock')
    if (!payload.value.ok) error.value = payload.value.message || '新股数据读取失败'
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '新股数据读取失败'
  } finally {
    loading.value = false
  }
}

async function refreshListings() {
  refreshing.value = true
  message.value = '刷新任务已提交…'
  try {
    const result = await apiPost<{ ok?: boolean; message?: string }>('/jobs', { job: 'listings' })
    if (result.ok === false) throw new Error(result.message || '任务启动失败')
    for (let attempt = 0; attempt < 60; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 1000))
      const jobs = await apiGet<{
        running?: { key: string } | null
        recent?: Array<{ key: string; status: string; error?: string }>
      }>('/jobs')
      if (jobs.running) continue
      const recent = jobs.recent?.[0]
      if (!recent || recent.key !== 'listings') continue
      if (recent.status !== 'done') throw new Error(recent.error || '刷新失败')
      await loadListings()
      message.value = '新股判定已刷新'
      return
    }
    throw new Error('刷新任务超时')
  } catch (cause) {
    message.value = cause instanceof Error ? cause.message : '刷新失败'
  } finally {
    refreshing.value = false
  }
}

async function selectInstrument(code: string) {
  app.setSelectedCode(code)
  await router.push('/instrument')
}

async function addInstrument(code: string) {
  message.value = ''
  try {
    await app.addInstrument(code)
    message.value = `${code} 已加入观察池`
  } catch (cause) {
    message.value = cause instanceof Error ? cause.message : '添加失败'
  }
}

onMounted(loadListings)
</script>

<template>
  <div class="view-stack">
    <PageHeader
      eyebrow="NEW LISTINGS"
      title="新股与次新"
      description="这些标的因历史不足无法进入常规筛选池，单独观察上市时间、连板和累计涨幅。"
    >
      <template #actions>
        <button class="secondary-button" type="button" :disabled="loading || refreshing" @click="refreshListings">
          <RefreshCw :size="15" :class="{ spinning: loading || refreshing }" />
          刷新判定
        </button>
      </template>
    </PageHeader>

    <p v-if="error" class="alert-banner is-danger">{{ error }}</p>
    <p v-if="message" class="inline-note">{{ message }}</p>
    <StatusGrid :items="statusItems" />

    <section class="surface-card">
      <div class="section-heading">
        <div>
          <span class="section-kicker">NEW · ≤ {{ payload?.new_days || 30 }} DAYS</span>
          <h3>新股</h3>
        </div>
        <StatusPill tone="warning">{{ payload?.new?.length || 0 }} 只展示</StatusPill>
      </div>
      <div v-if="!payload?.new?.length" class="empty-state compact">当前没有新股命中。</div>
      <div v-else class="data-table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>标的</th>
              <th class="num">上市日</th>
              <th class="num">交易天数</th>
              <th class="num">开盘连板</th>
              <th class="num">上市以来</th>
              <th class="num">最新价</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in payload.new" :key="item.code">
              <td><button class="table-link" type="button" @click="selectInstrument(item.code)">{{ item.name }}</button><small class="cell-subtitle num">{{ item.code }} · {{ item.board || '' }}</small></td>
              <td class="num">{{ item.listed_date }}</td>
              <td class="num">{{ item.trading_days }}<small v-if="item.estimated" class="cell-subtitle warning-text">日期估算</small></td>
              <td class="num">{{ item.boards_from_start || 0 }} 连板</td>
              <td class="num" :class="{ positive: (item.since_list_pct || 0) > 0, negative: (item.since_list_pct || 0) < 0 }">{{ fmt(item.since_list_pct, 2) }}%</td>
              <td class="num">{{ fmt(item.last_close, 3) }}</td>
              <td><button class="inline-button" type="button" @click="addInstrument(item.code)"><Plus :size="12" /> 加入观察</button></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="surface-card">
      <div class="section-heading">
        <div>
          <span class="section-kicker">RECENT · ≤ {{ payload?.recent_days || 250 }} DAYS</span>
          <h3>次新</h3>
        </div>
        <StatusPill>{{ payload?.recent?.length || 0 }} 只展示</StatusPill>
      </div>
      <div v-if="!payload?.recent?.length" class="empty-state compact">当前没有次新股命中。</div>
      <div v-else class="data-table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>标的</th>
              <th class="num">上市日</th>
              <th class="num">交易天数</th>
              <th class="num">开盘连板</th>
              <th class="num">上市以来</th>
              <th class="num">最新价</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in payload.recent" :key="item.code">
              <td><button class="table-link" type="button" @click="selectInstrument(item.code)">{{ item.name }}</button><small class="cell-subtitle num">{{ item.code }} · {{ item.board || '' }}</small></td>
              <td class="num">{{ item.listed_date }}</td>
              <td class="num">{{ item.trading_days }}<small v-if="item.estimated" class="cell-subtitle warning-text">日期估算</small></td>
              <td class="num">{{ item.boards_from_start || 0 }} 连板</td>
              <td class="num" :class="{ positive: (item.since_list_pct || 0) > 0, negative: (item.since_list_pct || 0) < 0 }">{{ fmt(item.since_list_pct, 2) }}%</td>
              <td class="num">{{ fmt(item.last_close, 3) }}</td>
              <td><button class="inline-button" type="button" @click="addInstrument(item.code)"><Plus :size="12" /> 加入观察</button></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>
