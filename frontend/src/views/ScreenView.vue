<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RefreshCw } from '@lucide/vue'
import { useRouter } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import StatusPill from '../components/StatusPill.vue'
import CandidateTable from '../components/screen/CandidateTable.vue'
import MemberTable from '../components/screen/MemberTable.vue'
import OutcomeTable from '../components/screen/OutcomeTable.vue'
import { ApiError, apiGet, apiPost } from '../services/api'
import { fmt } from '../services/format'
import { useAppStore } from '../stores/app'

type PoolPayload = {
  ok?: boolean
  message?: string
  days?: number
  trade_date?: string
  min_hits?: number
  min_avg_amount?: number
  cost?: number
  screen_stale?: boolean
  screen_gap_days?: number
  exit_streak?: number
  candidates?: Array<Record<string, any>>
  candidate_total?: number
  members?: Array<Record<string, any>>
  outcomes?: Array<Record<string, any>>
  pattern_stats?: { patterns?: Array<Record<string, any>>; baseline?: string; tests?: number }
  asset_mix?: {
    score?: number
    stance?: string
    available?: boolean
    weights?: Record<string, number>
    preferred_label?: string
  }
  kind_counts?: Record<string, number>
  market?: {
    trade_date: string
    exact: boolean
    up_ratio?: number
    limit_up_count?: number
    limit_down_count?: number
    broken_limit_count?: number
    max_boards?: number
    total_amount?: number
    sentiment_stage?: string
    sentiment_note?: string
  } | null
  view?: Record<string, any>
}

const router = useRouter()
const app = useAppStore()
const payload = ref<PoolPayload | null>(null)
const loading = ref(false)
const actionMessage = ref('')
const error = ref('')
const kind = ref('all')
const marketSort = ref(false)
const liquidOnly = ref(false)
const actionableOnly = ref(false)
const minHits = ref(2)
const jobMessage = ref('')
const screening = ref(false)

const candidates = computed(() => payload.value?.candidates || [])
const members = computed(() => payload.value?.members || [])
const outcomes = computed(() => payload.value?.outcomes || [])
const patterns = computed(() => payload.value?.pattern_stats?.patterns || [])
const counts = computed(() => ({
  all: 0,
  stock: 0,
  etf: 0,
  broad_etf: 0,
  sector_etf: 0,
  index: 0,
  ...(payload.value?.kind_counts || {}),
}))
const isEtf = computed(() => ['etf', 'broad_etf', 'sector_etf'].includes(kind.value))

function readFilters() {
  const params = new URLSearchParams(window.location.search)
  const incoming = params.get('kind') || 'all'
  kind.value = ['all', 'stock', 'etf', 'broad_etf', 'sector_etf', 'index'].includes(incoming)
    ? incoming
    : 'all'
  marketSort.value = params.get('sort') === 'market'
  liquidOnly.value = params.get('liquid') === '1'
  actionableOnly.value = params.get('actionable') === '1'
  minHits.value = [2, 3, 5].includes(Number(params.get('min_hits'))) ? Number(params.get('min_hits')) : 2
}

function queryString() {
  const params = new URLSearchParams()
  if (kind.value !== 'all') params.set('kind', kind.value)
  if (marketSort.value) params.set('sort', 'market')
  if (liquidOnly.value) params.set('liquid', '1')
  if (actionableOnly.value) params.set('actionable', '1')
  if (minHits.value !== 2) params.set('min_hits', String(minHits.value))
  return params.toString()
}

function syncUrl() {
  const query = queryString()
  const path = `${window.location.pathname}${query ? `?${query}` : ''}`
  window.history.replaceState({}, '', path)
}

function applyFilters() {
  syncUrl()
  void loadPool()
}

function setKind(value: string) {
  kind.value = value
  applyFilters()
}

function amountShort(value?: number | null) {
  if (value == null) return '—'
  return value >= 1e12 ? `${(value / 1e12).toFixed(2)} 万亿` : `${(value / 1e8).toFixed(0)} 亿`
}

async function loadPool() {
  loading.value = true
  error.value = ''
  try {
    const query = queryString()
    payload.value = await apiGet<PoolPayload>(`/pool${query ? `?${query}` : ''}`)
    if (payload.value.ok === false) error.value = payload.value.message || '筛选池读取失败'
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '筛选池读取失败'
  } finally {
    loading.value = false
  }
}

async function rerunScreen() {
  if (screening.value) return
  screening.value = true
  jobMessage.value = '正在重跑全市场筛选…'
  try {
    const started = await apiPost<{ ok?: boolean; message?: string }>('/jobs', { job: 'screen' })
    if (started.ok === false) throw new Error(started.message || '筛选任务启动失败')
    for (let attempt = 0; attempt < 160; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 1500))
      const jobs = await apiGet<{ running?: unknown; recent?: Array<Record<string, any>> }>('/jobs')
      if (!jobs.running) {
        const last = jobs.recent?.[0]
        if (last?.status === 'failed') throw new Error(last.error || '筛选失败')
        jobMessage.value = '筛选已完成，正在刷新候选…'
        await loadPool()
        jobMessage.value = '筛选已更新'
        return
      }
    }
    throw new Error('筛选任务超时，请到「系统与数据」查看日志')
  } catch (cause) {
    jobMessage.value = cause instanceof Error ? cause.message : '重跑筛选失败'
  } finally {
    screening.value = false
  }
}

async function addCandidate(code: string) {
  actionMessage.value = ''
  try {
    await app.addInstrument(code)
    actionMessage.value = `${code} 已加入观察池`
    await loadPool()
  } catch (cause) {
    actionMessage.value = cause instanceof Error ? cause.message : '加入失败'
  }
}

async function removeMember(code: string) {
  actionMessage.value = ''
  try {
    await app.removeInstrument(code)
    actionMessage.value = `${code} 已移出观察池`
    await loadPool()
  } catch (cause) {
    actionMessage.value = cause instanceof Error ? cause.message : '移出失败'
  }
}

async function selectInstrument(code: string) {
  app.setSelectedCode(code)
  await router.push('/instrument')
}

onMounted(() => {
  readFilters()
  void loadPool()
})
</script>

<template>
  <div class="view-stack">
    <PageHeader
      eyebrow="SCREEN POOL"
      title="筛选池"
      description="把反复命中的标的、观察池现状和历史表现放在一起。页面只复述后端口径，不自行排序。"
    >
      <template #actions>
        <button class="secondary-button" type="button" :disabled="screening" @click="rerunScreen">
          <RefreshCw :size="15" :class="{ spinning: screening }" />
          {{ screening ? '筛选中…' : '重跑筛选' }}
        </button>
        <button class="secondary-button" type="button" :disabled="loading" @click="loadPool">
          <RefreshCw :size="15" :class="{ spinning: loading }" />
          刷新
        </button>
      </template>
    </PageHeader>

    <p v-if="error" class="alert-banner is-danger">{{ error }}</p>
    <p v-if="actionMessage" class="inline-note">{{ actionMessage }}</p>
    <p v-if="jobMessage" class="inline-note">{{ jobMessage }}</p>

    <div class="metric-grid">
      <div class="metric-card">
        <span class="metric-label">回看窗口</span>
        <strong class="metric-value num">{{ payload?.days || 0 }}</strong>
        <span class="metric-detail">个筛选日</span>
      </div>
      <div class="metric-card">
        <span class="metric-label">候选</span>
        <strong class="metric-value num">{{ candidates.length }}</strong>
        <span class="metric-detail">
          当前视图 / 共 {{ payload?.candidate_total || candidates.length }} 只 · 至少命中 {{ minHits }} 次
        </span>
      </div>
      <div class="metric-card">
        <span class="metric-label">观察池</span>
        <strong class="metric-value num">{{ members.length }}</strong>
        <span class="metric-detail">连续未命中 {{ payload?.exit_streak || 0 }} 次提示移出</span>
      </div>
      <div class="metric-card">
        <span class="metric-label">往返成本</span>
        <strong class="metric-value num">{{ fmt(payload?.cost, 3) }}%</strong>
        <span class="metric-detail">净收益率已经扣除</span>
      </div>
    </div>

    <p v-if="payload?.screen_stale" class="alert-banner">
      最近一次筛选距今 {{ payload.screen_gap_days || 0 }} 个交易日，出池提示暂时关闭。
    </p>

    <section class="surface-card pool-market-card">
      <div class="section-heading">
        <div>
          <span class="section-kicker">MARKET CONTEXT</span>
          <h3>筛选快照与市场姿态</h3>
        </div>
        <StatusPill :tone="payload?.asset_mix?.available ? 'positive' : 'warning'">
          {{ payload?.asset_mix?.available ? '市场层已评分' : '市场层未评分' }}
        </StatusPill>
      </div>
      <div class="pool-market-grid">
        <div>
          <span>筛选快照</span>
          <strong class="num">{{ payload?.trade_date || '—' }}</strong>
          <small>回看 {{ payload?.days || 0 }} 个筛选日</small>
        </div>
        <div>
          <span>市场层</span>
          <strong>
            {{ payload?.asset_mix?.available ? fmt(payload?.asset_mix?.score, 2) : '未评分' }}
          </strong>
          <small>{{ payload?.asset_mix?.stance || '—' }} · {{ payload?.asset_mix?.preferred_label || '—' }}</small>
        </div>
        <div v-if="payload?.market">
          <span>最近广度（{{ payload.market.trade_date }}）</span>
          <strong class="num">
            涨 {{ fmt((payload.market.up_ratio || 0) * 100, 1) }}% ·
            涨停 {{ payload.market.limit_up_count ?? '—' }} ·
            跌停 {{ payload.market.limit_down_count ?? '—' }}
          </strong>
          <small>
            炸板 {{ payload.market.broken_limit_count ?? '—' }} ·
            最高 {{ payload.market.max_boards ?? '—' }} 连板 ·
            {{ amountShort(payload.market.total_amount) }}
          </small>
        </div>
        <div>
          <span>情绪阶段</span>
          <strong>{{ payload?.market?.sentiment_stage || '未定' }}</strong>
          <small>{{ payload?.market?.sentiment_note || '情绪只调姿态，不产生选股信号' }}</small>
        </div>
      </div>
      <p v-if="payload?.market && !payload.market.exact" class="inline-note is-warning">
        筛选日 {{ payload.trade_date }} 没有可用广度，这里显示最近 {{ payload.market.trade_date }} 的数据；
        市场层因此未评分，不会假装成“中性”。
      </p>
      <p v-else-if="!payload?.market" class="inline-note is-warning">
        本地还没有市场广度数据；先跑日终或 breadth。候选仍按证据展示，不自动隐藏。
      </p>
    </section>

    <section class="surface-card">
      <div class="section-heading">
        <div>
          <span class="section-kicker">CANDIDATES</span>
          <h3>候选清单</h3>
        </div>
        <StatusPill>{{ candidates.length }} 只</StatusPill>
      </div>
      <div class="pool-toolbar">
        <div class="segmented-control">
          <button type="button" :class="{ active: kind === 'all' }" @click="setKind('all')">
            全部 {{ counts.all }}
          </button>
          <button type="button" :class="{ active: kind === 'stock' }" @click="setKind('stock')">
            个股 {{ counts.stock }}
          </button>
          <button type="button" :class="{ active: isEtf }" @click="setKind('etf')">
            ETF {{ counts.etf }}
          </button>
          <button type="button" :class="{ active: kind === 'index' }" @click="setKind('index')">
            指数 {{ counts.index }}
          </button>
        </div>
        <div v-if="isEtf" class="segmented-control">
          <button type="button" :class="{ active: kind === 'etf' }" @click="setKind('etf')">
            全部 ETF
          </button>
          <button type="button" :class="{ active: kind === 'broad_etf' }" @click="setKind('broad_etf')">
            宽基 {{ counts.broad_etf }}
          </button>
          <button type="button" :class="{ active: kind === 'sector_etf' }" @click="setKind('sector_etf')">
            行业 {{ counts.sector_etf }}
          </button>
        </div>
        <div class="segmented-control">
          <button type="button" :class="{ active: !marketSort }" @click="marketSort = false; applyFilters()">
            证据优先
          </button>
          <button type="button" :class="{ active: marketSort }" @click="marketSort = true; applyFilters()">
            市场层优先
          </button>
        </div>
        <label class="pool-check">
          <input v-model="liquidOnly" type="checkbox" @change="applyFilters" />
          只看流动性达标
        </label>
        <label class="pool-check">
          <input v-model="actionableOnly" type="checkbox" @change="applyFilters" />
          只看已算风险且可建仓
        </label>
        <label class="pool-check">
          命中 ≥
          <select v-model.number="minHits" @change="applyFilters">
            <option :value="2">2</option>
            <option :value="3">3</option>
            <option :value="5">5</option>
          </select>
        </label>
      </div>
      <p class="inline-note">
        筛选与排序由后端执行；切换类型或排序只改变观察视图，不改变候选资格和历史统计。
        市场层偏好只影响顺序，不放松筛选口径。
      </p>
      <CandidateTable :rows="candidates" @add="addCandidate" @select="selectInstrument" />
    </section>

    <section class="surface-card">
      <div class="section-heading">
        <div>
          <span class="section-kicker">WATCHLIST</span>
          <h3>池内现状</h3>
        </div>
        <StatusPill>{{ members.length }} 只</StatusPill>
      </div>
      <p class="inline-note">
        指数与 ETF 按固定定义配置，不参与形态轮换；持仓去留由仓位决定，不因一次未命中直接提示移出。
      </p>
      <MemberTable :rows="members" @drop="removeMember" @select="selectInstrument" />
    </section>

    <section class="surface-card">
      <div class="section-heading">
        <div>
          <span class="section-kicker">OUTCOMES</span>
          <h3>命中后的实际表现</h3>
        </div>
        <StatusPill>毛 / 净并列</StatusPill>
      </div>
      <OutcomeTable :rows="outcomes" :patterns="patterns" />
      <p class="footnote">
        净收益和净胜率已扣 {{ fmt(payload?.cost, 3) }}% 往返成本；样本不足的形态只适合当方向看。
      </p>
    </section>
  </div>
</template>
