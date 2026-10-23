<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Database, RefreshCw, ShieldAlert } from '@lucide/vue'

import PageHeader from '../components/PageHeader.vue'
import StatusPill from '../components/StatusPill.vue'
import StatusGrid from '../components/research/StatusGrid.vue'
import { ApiError, apiGet } from '../services/api'
import { fmt, signed } from '../services/format'

type FactorItem = {
  factor_id: string
  name: string
  layer?: string | null
  category?: string | null
  weight?: number | null
  status: string
  days?: number
  last_date?: string | null
  ic5?: number | null
  ic20?: number | null
  ic20_partial?: number | null
  max_corr?: number | null
  max_corr_with?: string | null
  verdict?: string | null
}

type FactorsPayload = {
  ok: boolean
  as_of?: string | null
  items?: FactorItem[]
  by_status?: Record<string, number>
  shadow_days?: number
  duplicate_corr?: number
  max_days?: number
  violations?: string[]
  note?: string
}

type AnalysisPayload = {
  item?: { answer: string; model: string; trade_date: string; tokens: number } | null
}

const payload = ref<FactorsPayload | null>(null)
const analysis = ref<AnalysisPayload | null>(null)
const loading = ref(false)
const error = ref('')

const items = computed(() => payload.value?.items || [])
const shadows = computed(() => items.value.filter((item) => item.status === 'shadow'))
const statusItems = computed(() => [
  {
    label: '生效因子',
    value: payload.value?.by_status?.active || 0,
    detail: '参与当日打分',
    tone: 'positive' as const,
  },
  {
    label: '影子因子',
    value: payload.value?.by_status?.shadow || 0,
    detail: `至少观察 ${payload.value?.shadow_days || 20} 个交易日`,
    tone: 'warning' as const,
  },
  {
    label: '最长覆盖',
    value: payload.value?.max_days || 0,
    detail: '交易日',
  },
  {
    label: '预算违规',
    value: payload.value?.violations?.length || 0,
    detail: '分类权重与相关性约束',
    tone: (payload.value?.violations?.length || 0) ? 'danger' as const : 'positive' as const,
  },
])

function statusLabel(status: string) {
  return { active: '生效', shadow: '影子', candidate: '候选', retired: '退役' }[status] || status
}

async function loadFactors() {
  loading.value = true
  error.value = ''
  try {
    const [factors, ai] = await Promise.all([
      apiGet<FactorsPayload>('/factors'),
      apiGet<AnalysisPayload>('/analysis?scope=ledger').catch(() => null),
    ])
    payload.value = factors
    analysis.value = ai
    if (!factors.ok) error.value = factors.note || '因子台账读取失败'
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '因子台账读取失败'
  } finally {
    loading.value = false
  }
}

onMounted(loadFactors)
</script>

<template>
  <div class="view-stack">
    <PageHeader
      eyebrow="FACTOR LEDGER"
      title="因子台账"
      description="谁在参与打分、权重多大、是否独立，以及影子因子离转正还有多少距离。"
    >
      <template #actions>
        <button class="secondary-button" type="button" :disabled="loading" @click="loadFactors">
          <RefreshCw :size="15" :class="{ spinning: loading }" />
          刷新台账
        </button>
      </template>
    </PageHeader>

    <p v-if="error" class="alert-banner is-danger">{{ error }}</p>

    <StatusGrid :items="statusItems" />

    <section class="surface-card">
      <div class="section-heading">
        <div>
          <span class="section-kicker">REGISTRY</span>
          <h3>因子清单</h3>
        </div>
        <StatusPill>数据日 {{ payload?.as_of || '—' }}</StatusPill>
      </div>
      <div class="data-table-wrap">
        <table class="data-table factor-ledger-table">
          <thead>
            <tr>
              <th>因子</th>
              <th>层 / 类别</th>
              <th class="num">权重</th>
              <th>状态</th>
              <th class="num">覆盖</th>
              <th class="num">IC5</th>
              <th class="num">IC20</th>
              <th class="num">残差 IC20</th>
              <th class="num">最大相关</th>
              <th>结论</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in items" :key="item.factor_id">
              <td>
                <strong>{{ item.name }}</strong>
                <small class="cell-subtitle num">{{ item.factor_id }}</small>
              </td>
              <td>{{ item.layer || '—' }}<small class="cell-subtitle">{{ item.category || '—' }}</small></td>
              <td class="num">{{ item.weight == null ? '—' : fmt(item.weight, 2) }}</td>
              <td><StatusPill :tone="item.status === 'active' ? 'positive' : 'warning'">{{ statusLabel(item.status) }}</StatusPill></td>
              <td class="num">{{ item.days || 0 }}<small class="cell-subtitle">{{ item.last_date?.slice(5) || '' }}</small></td>
              <td class="num" :class="{ positive: (item.ic5 || 0) > 0, negative: (item.ic5 || 0) < 0 }">{{ signed(item.ic5, 3) }}</td>
              <td class="num" :class="{ positive: (item.ic20 || 0) > 0, negative: (item.ic20 || 0) < 0 }">{{ signed(item.ic20, 3) }}</td>
              <td class="num">{{ signed(item.ic20_partial, 3) }}</td>
              <td class="num">{{ signed(item.max_corr, 2) }}<small class="cell-subtitle">{{ item.max_corr_with || '' }}</small></td>
              <td>{{ item.verdict || '—' }}</td>
            </tr>
            <tr v-if="!items.length">
              <td colspan="10" class="empty-cell">还没有因子数据，先运行每日任务。</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="footnote">
        权重写入的是当前生效权重。影子因子权重为 0，只记录贡献分与证据，不参与打分。
      </p>
    </section>

    <section v-if="payload?.violations?.length" class="alert-banner is-danger">
      <ShieldAlert :size="16" />
      {{ payload.violations.join('；') }}
    </section>

    <section>
      <div class="section-heading">
        <div>
          <span class="section-kicker">SHADOW PROMOTION</span>
          <h3>影子因子转正进度</h3>
        </div>
        <StatusPill>相关上限 {{ fmt(payload?.duplicate_corr, 2) }}</StatusPill>
      </div>
      <div class="shadow-grid">
        <article v-for="item in shadows" :key="item.factor_id" class="surface-card shadow-card">
          <div class="section-heading compact-heading">
            <div>
              <span class="section-kicker">{{ item.factor_id }}</span>
              <h3>{{ item.name }}</h3>
            </div>
            <span class="num">{{ item.days || 0 }} / {{ payload?.shadow_days || 20 }}</span>
          </div>
          <div class="progress-track">
            <span :style="{ width: `${Math.min(100, ((item.days || 0) / (payload?.shadow_days || 20)) * 100)}%` }" />
          </div>
          <dl class="shadow-stats">
            <div><dt>20 日 IC</dt><dd class="num">{{ signed(item.ic20, 3) }}</dd></div>
            <div><dt>最大相关</dt><dd class="num">{{ signed(item.max_corr, 2) }} {{ item.max_corr_with || '' }}</dd></div>
            <div><dt>结论</dt><dd>{{ item.verdict || '—' }}</dd></div>
          </dl>
        </article>
        <div v-if="!shadows.length" class="surface-card empty-state">暂无影子因子。</div>
      </div>
    </section>

    <section class="surface-card">
      <div class="section-heading">
        <div>
          <span class="section-kicker">AI SIDE NOTE</span>
          <h3>台账旁注</h3>
        </div>
        <Database :size="18" />
      </div>
      <article v-if="analysis?.item" class="analysis-answer">
        <p>{{ analysis.item.answer }}</p>
        <footer class="num">{{ analysis.item.model }} · {{ analysis.item.trade_date }} · {{ analysis.item.tokens }} tokens</footer>
      </article>
      <div v-else class="empty-state compact">还没有因子台账分析。旁注不参与打分。</div>
    </section>
  </div>
</template>
