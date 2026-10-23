<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { AlertTriangle, RefreshCw, ShieldCheck } from '@lucide/vue'

import MetricCard from '../components/MetricCard.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusPill from '../components/StatusPill.vue'
import { ApiError, apiGet } from '../services/api'

type OpenTrade = {
  id: number
  code: string
  name?: string | null
  framework: string
  theme?: string | null
  entry_price: number
  stop_price?: number | null
  risk_pct?: number | null
  position_pct?: number | null
}

type JournalPayload = {
  ok?: boolean
  message?: string
  open?: OpenTrade[]
  risk?: {
    open_count?: number
    total_risk_pct?: number
    missing_risk?: number
    limits?: {
      portfolio_risk_pct?: number
      single_risk_pct?: number
    }
    breaches?: Array<{
      kind: string
      key: string
      value: number
      limit: number
    }>
  }
  stats?: {
    closed?: number
    win_rate?: number | null
    expectancy?: number | null
    violations?: number
  }
}

const journal = ref<JournalPayload | null>(null)
const loading = ref(true)
const error = ref('')

const openTrades = computed(() => journal.value?.open || [])
const risk = computed(() => journal.value?.risk || {})
const limits = computed(() => risk.value.limits || {})
const breaches = computed(() => risk.value.breaches || [])
const stats = computed(() => journal.value?.stats || {})

async function loadJournal() {
  loading.value = true
  error.value = ''
  try {
    journal.value = await apiGet<JournalPayload>('/journal')
    if (journal.value.ok === false) {
      error.value = journal.value.message || '账本暂时不可用'
    }
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '无法读取交易账本'
  } finally {
    loading.value = false
  }
}

function frameworkLabel(value: string) {
  return { core: '核心', satellite: '卫星', short: '短线' }[value] || value
}

onMounted(loadJournal)
</script>

<template>
  <div class="view-stack">
    <PageHeader
      eyebrow="JOURNAL"
      title="交易账本"
      description="这里保持只读。开仓和解仓仍通过 CLI 写入，避免在页面上随手改动已经冻结的判断。"
    >
      <template #actions>
        <button class="secondary-button" type="button" :disabled="loading" @click="loadJournal">
          <RefreshCw :size="15" :class="{ spinning: loading }" />
          刷新
        </button>
      </template>
    </PageHeader>

    <p v-if="error" class="alert-banner is-danger">
      <AlertTriangle :size="16" />
      {{ error }}
    </p>

    <div class="metric-grid">
      <MetricCard
        label="未平仓"
        :value="risk.open_count || 0"
        detail="当前在险头寸"
      />
      <MetricCard
        label="组合在险"
        :value="`${Number(risk.total_risk_pct || 0).toFixed(2)}%`"
        :detail="`上限 ${Number(limits.portfolio_risk_pct || 0).toFixed(2)}%`"
        :tone="breaches.length ? 'danger' : 'positive'"
      />
      <MetricCard
        label="已平仓"
        :value="stats.closed || 0"
        :detail="`净期望 ${stats.expectancy == null ? '—' : `${stats.expectancy > 0 ? '+' : ''}${stats.expectancy} 元/笔`}`"
      />
      <MetricCard
        label="纪律违规"
        :value="stats.violations || 0"
        detail="与盈亏分开统计"
        :tone="(stats.violations || 0) > 0 ? 'warning' : 'positive'"
      />
    </div>

    <section class="surface-card">
      <div class="section-heading">
        <div>
          <span class="section-kicker">OPEN POSITIONS</span>
          <h3>未平仓记录</h3>
        </div>
        <StatusPill :tone="breaches.length ? 'danger' : 'positive'">
          {{ breaches.length ? `${breaches.length} 项越线` : '未触发风险线' }}
        </StatusPill>
      </div>

      <div v-if="loading" class="empty-state">正在读取账本…</div>
      <div v-else-if="!openTrades.length" class="empty-state">
        <ShieldCheck :size="22" />
        当前没有未平仓记录。
      </div>
      <div v-else class="data-table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>标的</th>
              <th>框架</th>
              <th>题材</th>
              <th class="num">入场</th>
              <th class="num">失效</th>
              <th class="num">单笔 R</th>
              <th class="num">仓位</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="trade in openTrades" :key="trade.id">
              <td>
                <strong>{{ trade.name || trade.code }}</strong>
                <small class="cell-subtitle num">{{ trade.code }}</small>
              </td>
              <td>{{ frameworkLabel(trade.framework) }}</td>
              <td>{{ trade.theme || '—' }}</td>
              <td class="num">{{ trade.entry_price.toFixed(3) }}</td>
              <td class="num">{{ trade.stop_price?.toFixed(3) || '—' }}</td>
              <td class="num">{{ trade.risk_pct == null ? '—' : `${trade.risk_pct.toFixed(2)}%` }}</td>
              <td class="num">{{ trade.position_pct == null ? '—' : `${trade.position_pct.toFixed(1)}%` }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="breaches.length" class="breach-list">
        <p v-for="breach in breaches" :key="`${breach.kind}-${breach.key}`">
          <AlertTriangle :size="14" />
          {{ breach.kind }} · {{ breach.key }}：{{ breach.value.toFixed(2) }}%，上限
          {{ breach.limit.toFixed(2) }}%
        </p>
      </div>
    </section>
  </div>
</template>
