<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { BarChart3, RefreshCw, Sparkles } from '@lucide/vue'

import EChart from '../components/EChart.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusPill from '../components/StatusPill.vue'
import AlertTable from '../components/instrument/AlertTable.vue'
import FactorTable from '../components/instrument/FactorTable.vue'
import LevelTable from '../components/instrument/LevelTable.vue'
import MetricStrip from '../components/instrument/MetricStrip.vue'
import { chartOptionForCandles, chartOptionForIntraday, type ChartBar } from '../charts/options'
import { useChartPalette } from '../composables/useChartPalette'
import { ApiError, apiGet, apiPost } from '../services/api'
import { fmt, pct } from '../services/format'
import { useAppStore } from '../stores/app'

type LevelRow = {
  level_type: string
  price_low: number
  price_high: number
  weight?: number
  engine?: string | null
}

type AlertRow = {
  trade_date: string
  level: string
  signal_type?: string | null
  message: string
  price?: number | null
}

type FactorRow = {
  factor_id: string
  name?: string | null
  category?: string | null
  role?: string | null
  status?: string | null
  raw_value?: number | null
  normalized_score?: number | null
  weight?: number | null
  contribution?: number | null
}

type InstrumentBar = ChartBar & {
  state?: string | null
  state_days?: number | null
  trend_score?: number | null
  opportunity_score?: number | null
  vol_ratio_20?: number | null
  position_cap?: number | null
  stop_level?: number | null
  risk_reward?: number | null
  risk_note?: string | null
  candle_pattern?: string | null
}

type KlinePayload = {
  code: string
  name: string
  type?: string | null
  factor_date?: string | null
  bars: InstrumentBar[]
  levels: LevelRow[]
  alerts: AlertRow[]
  factors: FactorRow[]
}

type QuotePayload = {
  trading?: boolean
  session?: string
  items?: Array<{
    code: string
    name: string
    close: number | null
    pct_chg: number | null
    pre_close?: number | null
    high?: number | null
    low?: number | null
    amount?: number | null
    quote_time?: string | null
  }>
}

type IntradayPayload = {
  points?: Array<{ t: string; p: number; avg: number; v: number }>
  prev_close?: number | null
  message?: string
}

type AnalysisPayload = {
  ok: boolean
  enabled?: boolean
  configured?: boolean
  item?: {
    trade_date: string
    model: string
    created_at: string
    answer: string
    tokens: number
    latency_ms: number
  } | null
}

type JobSnapshot = {
  running?: { title: string; key: string } | null
  recent?: Array<{ key: string; status: string; title: string; error?: string }>
}

const app = useAppStore()
const palette = useChartPalette()
const days = ref(250)
const chartMode = ref<'kline' | 'intraday'>('kline')
const flags = reactive({
  movingAverages: true,
  bands: true,
  alerts: true,
  patterns: true,
  volume: true,
})
const data = ref<KlinePayload | null>(null)
const intraday = ref<IntradayPayload | null>(null)
const quote = ref<QuotePayload | null>(null)
const analysis = ref<AnalysisPayload | null>(null)
const loading = ref(false)
const analysisRunning = ref(false)
const error = ref('')
const analysisMessage = ref('')

const code = computed(() => app.selectedCode.value)
const latest = computed(() => data.value?.bars.at(-1) || null)
const quoteItem = computed(() => quote.value?.items?.find((item) => item.code === code.value))
const chartOption = computed(() => {
  if (chartMode.value === 'intraday') {
    return chartOptionForIntraday(intraday.value || {}, palette.value)
  }
  return chartOptionForCandles(
    data.value || { code: code.value, bars: [], levels: [], alerts: [] },
    flags,
    palette.value,
  )
})

const stateTone = computed(() => {
  if (latest.value?.state === 'up') return 'positive'
  if (latest.value?.state === 'down') return 'danger'
  return 'neutral'
})

async function loadInstrument() {
  if (!code.value) return
  loading.value = true
  error.value = ''
  try {
    const [kline, quotes, analysisResult] = await Promise.all([
      apiGet<KlinePayload>(`/kline?code=${encodeURIComponent(code.value)}&days=${days.value}`),
      apiGet<QuotePayload>('/quotes').catch(() => null),
      apiGet<AnalysisPayload>(
        `/analysis?scope=instrument&code=${encodeURIComponent(code.value)}`,
      ).catch(() => null),
    ])
    data.value = kline
    quote.value = quotes
    analysis.value = analysisResult
    if (chartMode.value === 'intraday') await loadIntraday()
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '单票数据读取失败'
  } finally {
    loading.value = false
  }
}

async function loadIntraday() {
  if (!code.value) return
  try {
    intraday.value = await apiGet<IntradayPayload>(
      `/intraday?code=${encodeURIComponent(code.value)}`,
    )
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '分时数据读取失败'
  }
}

async function switchChart(mode: 'kline' | 'intraday') {
  chartMode.value = mode
  if (mode === 'intraday' && !intraday.value) await loadIntraday()
}

async function refreshQuotes() {
  try {
    quote.value = await apiGet<QuotePayload>('/quotes')
    if (chartMode.value === 'intraday') await loadIntraday()
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '实时报价读取失败'
  }
}

async function runAnalysis() {
  if (!code.value || analysisRunning.value) return
  analysisRunning.value = true
  analysisMessage.value = '模型分析任务已提交，等待结果…'
  try {
    await apiPost('/jobs', { job: 'analyze', code: code.value, scope: 'instrument' })
    for (let attempt = 0; attempt < 120; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 1000))
      const jobs = await apiGet<JobSnapshot>('/jobs')
      if (jobs.running) continue
      const recent = jobs.recent?.[0]
      if (recent?.key !== 'analyze') continue
      if (recent.status !== 'done') throw new Error(recent.error || '分析任务失败')
      analysis.value = await apiGet<AnalysisPayload>(
        `/analysis?scope=instrument&code=${encodeURIComponent(code.value)}`,
      )
      analysisMessage.value = '分析已更新'
      return
    }
    throw new Error('分析任务超时')
  } catch (cause) {
    analysisMessage.value = cause instanceof Error ? cause.message : '分析任务失败'
  } finally {
    analysisRunning.value = false
  }
}

watch(code, () => {
  intraday.value = null
  loadInstrument()
})

watch(days, () => loadInstrument())

onMounted(async () => {
  if (!app.items.value.length) await app.refreshWatchlist().catch(() => undefined)
  await loadInstrument()
})
</script>

<template>
  <div class="view-stack">
    <PageHeader
      eyebrow="INSTRUMENT"
      title="单票工作台"
      :description="
        data
          ? `${data.name} · ${data.code} · ${latest?.trade_date || '暂无日线'}`
          : '从观察池选择一只标的，沿着状态、行情、位置和风险逐层判断。'
      "
    >
      <template #actions>
        <button class="secondary-button" type="button" :disabled="loading" @click="loadInstrument">
          <RefreshCw :size="15" :class="{ spinning: loading }" />
          刷新结论
        </button>
      </template>
    </PageHeader>

    <p v-if="error" class="alert-banner is-danger">{{ error }}</p>

    <section class="surface-card instrument-hero">
      <div class="instrument-title">
        <div>
          <span class="section-kicker">LATEST DECISION</span>
          <h3>{{ data?.name || code || '请选择标的' }}</h3>
          <small class="num">{{ data?.type || '' }} · {{ latest?.trade_date || '暂无日线' }}</small>
        </div>
        <div class="instrument-price">
          <strong class="num" :class="{ positive: (latest?.pct_chg || 0) > 0, negative: (latest?.pct_chg || 0) < 0 }">
            {{ fmt(quoteItem?.close ?? latest?.close, 3) }}
          </strong>
          <span class="num" :class="{ positive: (latest?.pct_chg || 0) > 0, negative: (latest?.pct_chg || 0) < 0 }">
            {{ pct(quoteItem?.pct_chg ?? latest?.pct_chg) }}
          </span>
          <StatusPill :tone="stateTone">
            {{ latest?.state_days ? `${latest.state_days} 日` : quote?.session || '收盘' }}
          </StatusPill>
        </div>
      </div>

      <MetricStrip :latest="latest as Record<string, unknown> | null" />

      <div v-if="latest && latest.state == null" class="inline-note is-warning">
        这一天的特征还没算：日线已经在库里，但状态、关键带和风险需要通过每日任务补齐。
      </div>
    </section>

    <section class="surface-card chart-card">
      <div class="chart-toolbar">
        <div class="segmented-control">
          <button type="button" :class="{ active: chartMode === 'kline' }" @click="switchChart('kline')">
            日 K
          </button>
          <button type="button" :class="{ active: chartMode === 'intraday' }" @click="switchChart('intraday')">
            分时
          </button>
        </div>
        <div class="range-switch">
          <button v-for="value in [60, 120, 250, 2000]" :key="value" type="button" :class="{ active: days === value }" @click="days = value">
            {{ value === 60 ? '3 月' : value === 120 ? '6 月' : value === 250 ? '1 年' : '全部' }}
          </button>
        </div>
        <button class="secondary-button" type="button" @click="refreshQuotes">
          <BarChart3 :size="15" />
          刷新行情
        </button>
      </div>

      <div v-if="chartMode === 'kline'" class="chart-options">
        <label><input v-model="flags.movingAverages" type="checkbox" /> 均线</label>
        <label><input v-model="flags.bands" type="checkbox" /> 关键带</label>
        <label><input v-model="flags.alerts" type="checkbox" /> 提醒</label>
        <label><input v-model="flags.patterns" type="checkbox" /> 形态</label>
        <label><input v-model="flags.volume" type="checkbox" /> 成交量</label>
      </div>

      <EChart :option="chartOption" :height="430" />
      <p v-if="chartMode === 'intraday' && intraday?.message" class="inline-note">{{ intraday.message }}</p>
    </section>

    <div class="instrument-panels">
      <section class="surface-card">
        <div class="section-heading">
          <div>
            <span class="section-kicker">FACTOR LEDGER</span>
            <h3>因子贡献</h3>
          </div>
          <small class="num">{{ data?.factor_date || '—' }}</small>
        </div>
        <FactorTable :rows="data?.factors || []" />
      </section>

      <section class="surface-card">
        <div class="section-heading">
          <div>
            <span class="section-kicker">PRICE STRUCTURE</span>
            <h3>关键带</h3>
          </div>
        </div>
        <LevelTable :rows="data?.levels || []" :close="latest?.close" />
      </section>

      <section class="surface-card">
        <div class="section-heading">
          <div>
            <span class="section-kicker">ALERTS</span>
            <h3>本标的提醒</h3>
          </div>
          <StatusPill>{{ data?.alerts?.length || 0 }} 条</StatusPill>
        </div>
        <AlertTable :rows="data?.alerts || []" />
      </section>
    </div>

    <section class="surface-card">
      <div class="section-heading">
        <div>
          <span class="section-kicker">AI SIDE NOTE</span>
          <h3>指标旁注</h3>
        </div>
        <button class="secondary-button" type="button" :disabled="analysisRunning || analysis?.configured === false" @click="runAnalysis">
          <Sparkles :size="15" />
          跑一次分析
        </button>
      </div>
      <p v-if="analysisMessage" class="inline-note">{{ analysisMessage }}</p>
      <article v-if="analysis?.item" class="analysis-answer">
        <p>{{ analysis.item.answer }}</p>
        <footer class="num">
          {{ analysis.item.model }} · {{ analysis.item.trade_date }} · {{ analysis.item.tokens }} tokens
        </footer>
      </article>
      <div v-else class="empty-state compact">
        还没有这项分析。分析只读本地已经算好的数字，不参与打分。
      </div>
    </section>
  </div>
</template>
