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

type ClosedTrade = {
  id: number
  code: string
  name?: string | null
  theme?: string | null
  entry_price: number
  exit_price?: number | null
  pnl?: number | null
  pnl_net?: number | null
  closed_on?: string | null
  discipline?: number | null
  alpha_beta?: string | null
  exit_note?: string | null
}

type JournalPayload = {
  ok?: boolean
  message?: string
  open?: OpenTrade[]
  closed?: ClosedTrade[]
  cost?: number
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
    n?: number
    win_rate?: number | null
    expectancy?: number | null
    violations?: number
  }
  unlock?: {
    tier?: number
    n?: number
    next_at?: number | null
    healthy?: boolean
  }
  guardrails?: {
    flags?: string[]
    month_pct?: number | null
    consecutive_losses?: number
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
const closed = computed(() => journal.value?.closed || [])
const unlock = computed(() => journal.value?.unlock || {})
const guardrails = computed(() => journal.value?.guardrails || {})

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

function disciplineLabel(value?: number | null) {
  return value === 1 ? '按纪律' : value === 0 ? '违规' : '未填'
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
      <MetricCard
        label="额度档位"
        :value="`${unlock.tier || 5}%`"
        :detail="unlock.next_at ? `再 ${Math.max(0, unlock.next_at - (unlock.n || 0))} 笔进下一档` : '已封顶'"
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

    <section v-if="(guardrails.flags || []).length" class="surface-card">
      <div class="section-heading">
        <div>
          <span class="section-kicker">GUARDRAILS</span>
          <h3>熔断与冷却</h3>
        </div>
        <StatusPill tone="warning">{{ guardrails.flags?.length }} 项</StatusPill>
      </div>
      <p v-for="flag in guardrails.flags || []" :key="flag" class="inline-note is-warning">{{ flag }}</p>
    </section>

    <section class="surface-card">
      <div class="section-heading">
        <div>
          <span class="section-kicker">CLOSED TRADES</span>
          <h3>已平仓</h3>
        </div>
        <StatusPill>{{ closed.length }} 条展示</StatusPill>
      </div>
      <div v-if="!closed.length" class="empty-state compact">还没有已平仓记录。</div>
      <div v-else class="data-table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>标的</th>
              <th class="num">平仓日</th>
              <th class="num">入场</th>
              <th class="num">出场</th>
              <th class="num">净盈亏</th>
              <th>纪律</th>
              <th>归因</th>
              <th>备注</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="trade in closed" :key="trade.id">
              <td><strong>{{ trade.name || trade.code }}</strong><small class="cell-subtitle num">{{ trade.code }} · {{ trade.theme || '—' }}</small></td>
              <td class="num">{{ trade.closed_on || '—' }}</td>
              <td class="num">{{ trade.entry_price.toFixed(3) }}</td>
              <td class="num">{{ trade.exit_price?.toFixed(3) || '—' }}</td>
              <td class="num" :class="{ positive: (trade.pnl_net || 0) > 0, negative: (trade.pnl_net || 0) < 0 }">
                {{ trade.pnl_net == null ? '—' : `${trade.pnl_net > 0 ? '+' : ''}${trade.pnl_net.toFixed(0)} 元` }}
              </td>
              <td>{{ disciplineLabel(trade.discipline) }}</td>
              <td>{{ trade.alpha_beta || '—' }}</td>
              <td>{{ trade.exit_note || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="footnote">往返成本 {{ journal?.cost == null ? '—' : `${journal.cost.toFixed(3)}%` }}；纪律执行与盈亏分开统计。</p>
    </section>
  </div>
</template>
