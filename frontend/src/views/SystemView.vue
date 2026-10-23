<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Database, RefreshCw, ServerCog } from '@lucide/vue'

import PageHeader from '../components/PageHeader.vue'
import StatusPill from '../components/StatusPill.vue'
import StatusGrid from '../components/research/StatusGrid.vue'
import JobRunner from '../components/system/JobRunner.vue'
import { ApiError, apiGet } from '../services/api'
import { useAppStore } from '../stores/app'

type HealthPayload = {
  process_started_at: string
  code_mtime: string
  stale: boolean
  routes: string[]
}

type MetaPayload = {
  feature_version?: string
  version?: string
}

const app = useAppStore()
const health = ref<HealthPayload | null>(null)
const meta = ref<MetaPayload | null>(null)
const loading = ref(false)
const error = ref('')

const statusItems = computed(() => [
  {
    label: '服务进程',
    value: health.value?.stale ? '需重启' : '正常',
    detail: health.value?.process_started_at || '—',
    tone: health.value?.stale ? 'warning' as const : 'positive' as const,
  },
  {
    label: '规则版本',
    value: meta.value?.feature_version || '—',
    detail: '权重和规则变更必须升级',
  },
  {
    label: '本地标的',
    value: app.coverage.value.instruments || 0,
    detail: '代码表覆盖',
  },
  {
    label: 'API 路由',
    value: health.value?.routes?.length || 0,
    detail: '当前服务已注册',
  },
])

async function loadSystem() {
  loading.value = true
  error.value = ''
  try {
    const [healthResult, metaResult] = await Promise.all([
      apiGet<HealthPayload>('/health'),
      apiGet<MetaPayload>('/meta'),
      app.refreshWatchlist(),
    ])
    health.value = healthResult
    meta.value = metaResult
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '系统状态读取失败'
  } finally {
    loading.value = false
  }
}

onMounted(loadSystem)
</script>

<template>
  <div class="view-stack">
    <PageHeader
      eyebrow="SYSTEM"
      title="系统与数据"
      description="服务健康、规则版本、数据覆盖和后台任务集中在这里，长任务不再散落在业务页面。"
    >
      <template #actions>
        <button class="secondary-button" type="button" :disabled="loading" @click="loadSystem">
          <RefreshCw :size="15" :class="{ spinning: loading }" />
          刷新体检
        </button>
      </template>
    </PageHeader>

    <p v-if="error" class="alert-banner is-danger">{{ error }}</p>
    <StatusGrid :items="statusItems" />

    <JobRunner />

    <div class="system-pair">
      <section class="surface-card">
        <div class="section-heading">
          <div>
            <span class="section-kicker">SERVICE</span>
            <h3>服务状态</h3>
          </div>
          <ServerCog :size="18" />
        </div>
        <dl class="system-details">
          <div><dt>进程启动</dt><dd class="num">{{ health?.process_started_at || '—' }}</dd></div>
          <div><dt>代码修改</dt><dd class="num">{{ health?.code_mtime || '—' }}</dd></div>
          <div><dt>是否需要重启</dt><dd>{{ health?.stale ? '是' : '否' }}</dd></div>
        </dl>
      </section>

      <section class="surface-card">
        <div class="section-heading">
          <div>
            <span class="section-kicker">DATA COVERAGE</span>
            <h3>本地数据</h3>
          </div>
          <Database :size="18" />
        </div>
        <dl class="system-details">
          <div><dt>标的代码</dt><dd class="num">{{ app.coverage.value.instruments || 0 }}</dd></div>
          <div><dt>股票</dt><dd class="num">{{ app.coverage.value.stocks || 0 }}</dd></div>
          <div><dt>ETF</dt><dd class="num">{{ app.coverage.value.etfs || 0 }}</dd></div>
          <div><dt>最新交易日</dt><dd class="num">{{ app.coverage.value.latest_trade_date || '—' }}</dd></div>
        </dl>
      </section>
    </div>

    <section class="surface-card">
      <div class="section-heading">
        <div>
          <span class="section-kicker">API SURFACE</span>
          <h3>已注册路由</h3>
        </div>
        <StatusPill>{{ health?.routes?.length || 0 }}</StatusPill>
      </div>
      <div class="api-routes">
        <code v-for="route in health?.routes || []" :key="route">{{ route }}</code>
      </div>
    </section>
  </div>
</template>
