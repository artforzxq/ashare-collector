<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { FlaskConical, RefreshCw } from '@lucide/vue'

import PageHeader from '../components/PageHeader.vue'
import StatusPill from '../components/StatusPill.vue'
import StatusGrid from '../components/research/StatusGrid.vue'
import { ApiError, apiGet } from '../services/api'
import { fmt, signed } from '../services/format'

type BacktestItem = {
  label?: string
  params?: Record<string, number>
  signals?: number
  blocked?: number
  win20?: number | null
  avg20?: number | null
  base20?: number | null
  excess20?: number | null
  mdd20?: number | null
  freq?: number | null
  plateau?: { mean?: number | null; worst?: number | null; positive?: number | null }
}

type BacktestPayload = {
  ok: boolean
  message?: string
  hint?: string
  saved_at?: string
  current?: Record<string, number>
  sample?: { mode?: string }
  years?: number
  cost?: number
  code_count?: number
  limit_check?: boolean
  items?: BacktestItem[]
  total?: number
  significance?: {
    ok?: boolean
    significant?: boolean
    searched?: number
    t?: number
    crit_t?: number
    p_adj?: number
  }
  turnover?: {
    ok?: boolean
    codes?: number
    years?: number
    covered?: number
    buckets?: Array<{
      bucket: string
      n20: number
      days20?: number
      excess_by_day20?: number
      t20?: number | null
      excess20?: number
      win20?: number | null
    }>
  }
}

type AnalysisPayload = {
  item?: { answer: string; model: string; trade_date: string; tokens: number } | null
}

const payload = ref<BacktestPayload | null>(null)
const analysis = ref<AnalysisPayload | null>(null)
const loading = ref(false)
const error = ref('')
const items = computed(() => payload.value?.items || [])
const current = computed(() => payload.value?.current || {})
const positiveCount = computed(() => items.value.filter((item) => (item.excess20 || 0) > 0).length)

const statusItems = computed(() => [
  {
    label: '参数组合',
    value: payload.value?.total || items.value.length,
    detail: '按 20 日净超额排序',
  },
  {
    label: '净超额为正',
    value: `${positiveCount.value} / ${items.value.length}`,
    detail: '展示的前 40 组',
    tone: positiveCount.value ? 'positive' as const : 'warning' as const,
  },
  {
    label: '样本',
    value: payload.value?.code_count || 0,
    detail: `${fmt(payload.value?.years, 1)} 年 · ${fmt(payload.value?.cost, 3)}% 成本`,
  },
  {
    label: '当前配置',
    value: current.value.enter_up ?? '—',
    detail: `确认 ${current.value.confirm_days ?? '—'} 日 · 最短 ${current.value.min_state_days ?? '—'} 日`,
  },
])

function isCurrent(item: BacktestItem) {
  const params = item.params || {}
  return (
    params.enter_up === current.value.enter_up &&
    params.confirm_days === current.value.confirm_days &&
    params.min_state_days === current.value.min_state_days
  )
}

async function loadBacktest() {
  loading.value = true
  error.value = ''
  try {
    const [backtest, ai] = await Promise.all([
      apiGet<BacktestPayload>('/backtest'),
      apiGet<AnalysisPayload>('/analysis?scope=backtest').catch(() => null),
    ])
    payload.value = backtest
    analysis.value = ai
    if (!backtest.ok) error.value = backtest.message || backtest.hint || '回测结果读取失败'
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '回测结果读取失败'
  } finally {
    loading.value = false
  }
}

onMounted(loadBacktest)
</script>

<template>
  <div class="view-stack">
    <PageHeader
      eyebrow="BACKTEST"
      title="参数回测"
      description="先看 20 日净超额、信号数和邻域稳定性，再判断一组参数是否值得保留。"
    >
      <template #actions>
        <button class="secondary-button" type="button" :disabled="loading" @click="loadBacktest">
          <RefreshCw :size="15" :class="{ spinning: loading }" />
          刷新结果
        </button>
      </template>
    </PageHeader>

    <p v-if="error" class="alert-banner">{{ error }}</p>

    <StatusGrid :items="statusItems" />

    <section class="surface-card">
      <div class="section-heading">
        <div>
          <span class="section-kicker">PARAMETER GRID</span>
          <h3>参数组合</h3>
        </div>
        <div class="heading-badges">
          <StatusPill v-if="payload?.limit_check" tone="positive">已检查可成交性</StatusPill>
          <StatusPill>成本 {{ fmt(payload?.cost, 3) }}%</StatusPill>
        </div>
      </div>
      <div class="data-table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>参数组合</th>
              <th class="num">信号数</th>
              <th class="num">20 日胜率</th>
              <th class="num">20 日均值</th>
              <th class="num">基准</th>
              <th class="num">净超额</th>
              <th class="num">平均回撤</th>
              <th class="num">年频次</th>
              <th>邻域</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in items" :key="item.label || JSON.stringify(item.params)" :class="{ 'current-row': isCurrent(item) }">
              <td><strong>{{ item.label || '参数组合' }}</strong><small v-if="isCurrent(item)" class="cell-subtitle accent-text">当前配置</small></td>
              <td class="num">{{ item.signals || 0 }}<small class="cell-subtitle">{{ item.blocked ? `剔除 ${item.blocked}` : '' }}</small></td>
              <td class="num">{{ fmt(item.win20, 1) }}%</td>
              <td class="num">{{ signed(item.avg20, 2) }}%</td>
              <td class="num">{{ signed(item.base20, 2) }}%</td>
              <td class="num" :class="{ positive: (item.excess20 || 0) > 0, negative: (item.excess20 || 0) < 0 }">{{ signed(item.excess20, 2) }}%</td>
              <td class="num">{{ signed(item.mdd20, 1) }}%</td>
              <td class="num">{{ fmt(item.freq, 1) }}</td>
              <td>
                <span v-if="item.plateau?.mean == null">—</span>
                <span v-else class="num">{{ signed(item.plateau.mean, 2) }} / 最差 {{ signed(item.plateau.worst, 2) }} / 为正 {{ Math.round(item.plateau.positive || 0) }}%</span>
              </td>
            </tr>
            <tr v-if="!items.length">
              <td colspan="9" class="empty-cell">{{ payload?.hint || '还没有可展示的回测组合。' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="payload?.significance?.ok" class="inline-note">
        搜索 {{ payload.significance.searched }} 组，最佳 t={{ fmt(payload.significance.t, 2) }}；
        {{ payload.significance.significant ? '校正后仍显著' : '校正后不显著' }}，p_adj={{ fmt(payload.significance.p_adj, 3) }}。
      </p>
    </section>

    <section class="surface-card">
      <div class="section-heading">
        <div>
          <span class="section-kicker">TURNOVER</span>
          <h3>换手率分组</h3>
        </div>
        <StatusPill v-if="payload?.turnover?.ok">{{ payload.turnover.codes }} 只 · {{ fmt(payload.turnover.years, 1) }} 年</StatusPill>
      </div>
      <div v-if="payload?.turnover?.ok" class="data-table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>分档</th>
              <th class="num">样本</th>
              <th class="num">交易日</th>
              <th class="num">按日净超额</th>
              <th class="num">t 值</th>
              <th class="num">按样本超额</th>
              <th class="num">20 日净胜率</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="bucket in payload.turnover.buckets || []" :key="bucket.bucket">
              <td><strong>{{ bucket.bucket }}</strong></td>
              <td class="num">{{ bucket.n20.toLocaleString() }}</td>
              <td class="num">{{ bucket.days20 ?? '—' }}</td>
              <td class="num" :class="{ positive: (bucket.excess_by_day20 || 0) > 0, negative: (bucket.excess_by_day20 || 0) < 0 }">{{ signed(bucket.excess_by_day20, 2) }}%</td>
              <td class="num">{{ fmt(bucket.t20, 2) }}</td>
              <td class="num">{{ signed(bucket.excess20, 2) }}%</td>
              <td class="num">{{ fmt(bucket.win20, 1) }}%</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="empty-state compact">还没有换手率研究。样本不足时不会强行给结论。</div>
    </section>

    <section class="surface-card">
      <div class="section-heading">
        <div>
          <span class="section-kicker">AI SIDE NOTE</span>
          <h3>回测旁注</h3>
        </div>
        <FlaskConical :size="18" />
      </div>
      <article v-if="analysis?.item" class="analysis-answer">
        <p>{{ analysis.item.answer }}</p>
        <footer class="num">{{ analysis.item.model }} · {{ analysis.item.trade_date }} · {{ analysis.item.tokens }} tokens</footer>
      </article>
      <div v-else class="empty-state compact">还没有回测旁注。模型只读摘要，不参与参数选择。</div>
    </section>
  </div>
</template>
