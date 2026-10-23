<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RefreshCw, ShieldCheck, Sparkles } from '@lucide/vue'
import { useRouter } from 'vue-router'

import FlowPanel from '../components/market/FlowPanel.vue'
import MainlinePanel from '../components/market/MainlinePanel.vue'
import MarketBreadth from '../components/market/MarketBreadth.vue'
import RankList from '../components/market/RankList.vue'
import SentimentPanel from '../components/market/SentimentPanel.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusPill from '../components/StatusPill.vue'
import { ApiError, apiGet } from '../services/api'
import { fmt } from '../services/format'
import { useAppStore } from '../stores/app'

type MarketPayload = {
  trade_date?: string | null
  sample?: number
  note?: string
  breadth?: {
    up: number
    down: number
    flat: number
    limit_up: number
    limit_down: number
    median_pct: number
    total_amount: number
  } | null
  distribution?: Array<{ label: string; count: number; direction?: 'down' | 'flat' | 'up' }>
  amount_stats?: any
  gainers?: Array<{ code: string; name: string; pct_chg: number }>
  losers?: Array<{ code: string; name: string; pct_chg: number }>
  sentiment?: Record<string, unknown> | null
  flow?: Record<string, unknown> | null
}

type MainlinePayload = {
  ok?: boolean
  message?: string
  coverage?: { coverage?: number; industries?: number }
  industries?: Array<{
    industry: string
    ret20: number
    share_trend_pp?: number | null
    diffusion: number
    top_weeks?: number
  }>
  mainlines?: Array<{ industry: string }>
  events?: Array<{ industry: string }>
}

type SupportPayload = {
  ok?: boolean
  message?: string
  today?: {
    date?: string
    triggered?: boolean
    totals?: { inflow?: number | null; volume_ratio?: number | null }
    items?: Array<{ code: string; name?: string; inflow?: number; volume_ratio?: number }>
  } | null
  history?: Array<{ trade_date: string; inflow?: number; volume_ratio?: number }>
}

type AnalysisPayload = {
  ok: boolean
  enabled?: boolean
  configured?: boolean
  item?: {
    trade_date: string
    model: string
    answer: string
    tokens: number
  } | null
}

const router = useRouter()
const app = useAppStore()
const market = ref<MarketPayload | null>(null)
const mainline = ref<MainlinePayload | null>(null)
const support = ref<SupportPayload | null>(null)
const analysis = ref<AnalysisPayload | null>(null)
const loading = ref(false)
const error = ref('')

const marketUpdated = computed(() => market.value?.trade_date || '暂无日期')
const supportItems = computed(() => support.value?.today?.items || [])

async function loadMarket() {
  loading.value = true
  error.value = ''
  try {
    const [marketResult, mainlineResult, supportResult, analysisResult] = await Promise.all([
      apiGet<MarketPayload>('/market'),
      apiGet<MainlinePayload>('/mainline').catch((cause) => ({
        ok: false,
        message: cause instanceof Error ? cause.message : '主线读取失败',
      })),
      apiGet<SupportPayload>('/support').catch((cause) => ({
        ok: false,
        message: cause instanceof Error ? cause.message : '托底读取失败',
      })),
      apiGet<AnalysisPayload>('/analysis?scope=market').catch(() => null),
    ])
    market.value = marketResult
    mainline.value = mainlineResult
    support.value = supportResult
    analysis.value = analysisResult
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '市场数据读取失败'
  } finally {
    loading.value = false
  }
}

async function selectInstrument(code: string) {
  app.setSelectedCode(code)
  await router.push('/instrument')
}

onMounted(loadMarket)
</script>

<template>
  <div class="view-stack">
    <PageHeader
      eyebrow="MARKET"
      title="市场环境与主线"
      :description="`市场概览、情绪阶段、行业主线和资金去向 · ${marketUpdated}`"
    >
      <template #actions>
        <button class="secondary-button" type="button" :disabled="loading" @click="loadMarket">
          <RefreshCw :size="15" :class="{ spinning: loading }" />
          刷新市场
        </button>
      </template>
    </PageHeader>

    <p v-if="error" class="alert-banner is-danger">{{ error }}</p>

    <MarketBreadth
      :trade-date="market?.trade_date"
      :sample="market?.sample"
      :breadth="market?.breadth"
      :distribution="market?.distribution"
      :note="market?.note"
      :amount-stats="market?.amount_stats"
    />

    <div class="market-pair">
      <SentimentPanel :sentiment="market?.sentiment" />
      <MainlinePanel :payload="mainline" />
    </div>

    <FlowPanel :flow="market?.flow" />

    <div class="market-pair">
      <RankList title="涨幅榜" :rows="market?.gainers || []" @select="selectInstrument" />
      <RankList title="跌幅榜" :rows="market?.losers || []" @select="selectInstrument" />
    </div>

    <section class="surface-card">
      <div class="section-heading">
        <div>
          <span class="section-kicker">SUPPORT · T+1</span>
          <h3>疑似托底</h3>
        </div>
        <StatusPill :tone="support?.today?.triggered ? 'positive' : 'neutral'">
          {{ support?.today?.triggered ? '今日触发' : '未触发' }}
        </StatusPill>
      </div>
      <p class="inline-note">
        宽基 ETF 份额净流入 {{ support?.today?.totals?.inflow == null ? '—' : `${(support.today.totals.inflow / 1e8).toFixed(2)} 亿` }}
        · 异常放量 {{ fmt(support?.today?.totals?.volume_ratio, 2) }}
      </p>
      <div v-if="supportItems.length" class="support-grid">
        <button v-for="item in supportItems.slice(0, 8)" :key="item.code" type="button" @click="selectInstrument(item.code)">
          <strong>{{ item.name || item.code }}</strong>
          <small class="num">{{ item.code }}</small>
          <span class="num">{{ item.inflow == null ? '—' : `${(item.inflow / 1e8).toFixed(2)} 亿` }}</span>
        </button>
      </div>
      <div v-else class="empty-state compact">{{ support?.message || '今天没有同时满足份额流入和异常放量的标的。' }}</div>
      <p class="footnote">份额是 T+1 披露，这是事后信号，不应显示成实时抄底提示。</p>
    </section>

    <section class="surface-card">
      <div class="section-heading">
        <div>
          <span class="section-kicker">AI SIDE NOTE</span>
          <h3>资金侧旁注</h3>
        </div>
        <ShieldCheck :size="18" />
      </div>
      <article v-if="analysis?.item" class="analysis-answer">
        <p>{{ analysis.item.answer }}</p>
        <footer class="num">{{ analysis.item.model }} · {{ analysis.item.trade_date }} · {{ analysis.item.tokens }} tokens</footer>
      </article>
      <div v-else class="empty-state compact">
        {{ analysis?.configured === false ? 'AI 模型尚未配置。' : '还没有资金侧分析。' }}
        只读本地指标，不参与打分。
      </div>
    </section>

    <RouterLink class="action-card" to="/system">
      <span class="action-icon"><Sparkles :size="20" /></span>
      <span>
        <small>DATA OPERATIONS</small>
        <strong>运行同步、筛选或每日任务</strong>
        <p>任务入口集中到“系统与数据”，避免市场页误触长任务。</p>
      </span>
      <span>前往系统</span>
    </RouterLink>
  </div>
</template>
