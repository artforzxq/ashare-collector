<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ArrowRight, Database, Eye, RefreshCw, ShieldCheck } from '@lucide/vue'
import { RouterLink } from 'vue-router'

import MetricCard from '../components/MetricCard.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusPill from '../components/StatusPill.vue'
import { ApiError, apiGet } from '../services/api'
import { useAppStore } from '../stores/app'

type HealthPayload = {
  process_started_at: string
  code_mtime: string
  stale: boolean
  routes: string[]
}

const health = ref<HealthPayload | null>(null)
const loading = ref(true)
const error = ref('')
const app = useAppStore()
const watchlist = computed(() => app.items.value)

const latestDate = computed(
  () =>
    watchlist.value
      .map((item) => item.last_date)
      .filter((value): value is string => Boolean(value))
      .sort()
      .at(-1) || '暂无',
)

const healthTone = computed(() => {
  if (error.value) return 'danger'
  if (health.value?.stale) return 'warning'
  return 'positive'
})

const healthLabel = computed(() => {
  if (loading.value) return '检查中'
  if (error.value) return '接口异常'
  if (health.value?.stale) return '需重启服务'
  return '运行正常'
})

const stateLabels: Record<string, string> = {
  up: '上升',
  range: '震荡',
  down: '下跌',
}

async function loadOverview() {
  loading.value = true
  error.value = ''
  try {
    const [healthResult] = await Promise.all([
      apiGet<HealthPayload>('/health'),
      app.refreshWatchlist(),
    ])
    health.value = healthResult
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '无法读取本地服务'
  } finally {
    loading.value = false
  }
}

onMounted(loadOverview)
</script>

<template>
  <div class="view-stack">
    <PageHeader
      eyebrow="TODAY"
      title="先看状态，再看机会"
      description="新版前端九个视图均已接通现有 JSON API。页面按决策任务组织，数据只读，写入仍走 CLI 与后端任务。"
    >
      <template #actions>
        <button class="secondary-button" type="button" :disabled="loading" @click="loadOverview">
          <RefreshCw :size="15" :class="{ spinning: loading }" />
          刷新
        </button>
      </template>
    </PageHeader>

    <div class="metric-grid">
      <MetricCard
        label="本地服务"
        :value="healthLabel"
        detail="API 健康与代码热更新状态"
        :tone="healthTone"
      />
      <MetricCard
        label="观察池"
        :value="watchlist.length"
        detail="当前可读取标的"
        tone="neutral"
      />
      <MetricCard label="最新日线" :value="latestDate" detail="所有观察标的中的最新日期" />
      <MetricCard
        label="前端迁移"
        value="完成"
        detail="9 个视图已接通 · 旧版 / 继续保留"
        tone="positive"
      />
    </div>

    <p v-if="error" class="alert-banner">
      <ShieldCheck :size="16" />
      {{ error }}
    </p>

    <div class="overview-grid">
      <section class="surface-card watch-preview">
        <div class="section-heading">
          <div>
            <span class="section-kicker">WATCHLIST</span>
            <h3>观察池快照</h3>
          </div>
          <StatusPill tone="neutral">{{ watchlist.length }} 只</StatusPill>
        </div>

        <div v-if="loading" class="empty-state">正在读取本地数据…</div>
        <div v-else-if="!watchlist.length" class="empty-state">观察池还没有标的。</div>
        <div v-else class="watch-table">
          <div v-for="item in watchlist.slice(0, 8)" :key="item.code" class="watch-row">
            <div>
              <strong>{{ item.name }}</strong>
              <span class="num">{{ item.code }}</span>
            </div>
            <span
              class="watch-change num"
              :class="{ positive: (item.pct_chg || 0) > 0, negative: (item.pct_chg || 0) < 0 }"
            >
              {{ item.pct_chg == null ? '—' : `${item.pct_chg > 0 ? '+' : ''}${item.pct_chg.toFixed(2)}%` }}
            </span>
            <StatusPill :tone="item.state === 'up' ? 'positive' : item.state === 'down' ? 'danger' : 'neutral'">
              {{ item.state ? stateLabels[item.state] || item.state : '无状态' }}
            </StatusPill>
          </div>
        </div>
      </section>

      <section class="surface-card">
        <div class="section-heading">
          <div>
            <span class="section-kicker">SURFACE</span>
            <h3>工作台覆盖</h3>
          </div>
          <Database :size="18" />
        </div>
        <ol class="roadmap">
          <li class="is-done">
            <span>01</span>
            <div>
              <strong>工程骨架</strong>
              <p>Vue、TypeScript、路由、主题、API 客户端</p>
            </div>
          </li>
          <li class="is-done">
            <span>02</span>
            <div>
              <strong>核心看盘</strong>
              <p>总览、单票、K 线、分时、市场与资金</p>
            </div>
          </li>
          <li class="is-done">
            <span>03</span>
            <div>
              <strong>研究与账本</strong>
              <p>筛选、因子、回测、新股、交易账本</p>
            </div>
          </li>
          <li class="is-done">
            <span>04</span>
            <div>
              <strong>系统与数据</strong>
              <p>健康、任务、路由与规则版本</p>
            </div>
          </li>
        </ol>
      </section>

      <RouterLink class="action-card" to="/journal">
        <span class="action-icon"><Eye :size="20" /></span>
        <span>
          <small>决策记录</small>
          <strong>查看交易账本</strong>
          <p>读取未平仓、组合在险与纪律统计；开仓和平仓仍由 CLI 负责。</p>
        </span>
        <ArrowRight :size="18" />
      </RouterLink>
    </div>
  </div>
</template>
