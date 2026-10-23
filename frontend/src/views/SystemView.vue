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
  route_details?: Array<{ path: string; methods: string[]; group: string; online: boolean }>
  route_summary?: {
    paths: number
    endpoints: number
    online: number
    by_method: Record<string, number>
    by_group: Array<{ group: string; paths: number; endpoints: number }>
  }
  page_routes?: Array<{ path: string; label: string; online: boolean }>
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
const routeSummary = computed(() => health.value?.route_summary)
const routeGroups = computed(() => {
  const groups = new Map<string, Array<{ method: string; path: string; online: boolean }>>()
  for (const route of health.value?.route_details || []) {
    const list = groups.get(route.group) || []
    for (const method of route.methods) {
      list.push({ method, path: route.path, online: route.online })
    }
    groups.set(route.group, list)
  }
  return [...groups.entries()].map(([group, routes]) => ({ group, routes }))
})

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
    value: routeSummary.value ? `${routeSummary.value.online}/${routeSummary.value.endpoints}` : '—',
    detail: routeSummary.value
      ? `${routeSummary.value.paths} 个路径 · GET ${routeSummary.value.by_method.GET || 0} · POST ${routeSummary.value.by_method.POST || 0}`
      : '当前服务已注册',
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
        <StatusPill :tone="routeSummary && routeSummary.online === routeSummary.endpoints ? 'positive' : 'warning'">
          {{ routeSummary ? `${routeSummary.online}/${routeSummary.endpoints} 在线` : '—' }}
        </StatusPill>
      </div>

      <div v-if="routeSummary" class="route-summary-grid">
        <div><span>方法端点</span><strong class="num">{{ routeSummary.endpoints }}</strong></div>
        <div><span>在线端点</span><strong class="num">{{ routeSummary.online }}</strong></div>
        <div><span>GET</span><strong class="num">{{ routeSummary.by_method.GET || 0 }}</strong></div>
        <div><span>POST</span><strong class="num">{{ routeSummary.by_method.POST || 0 }}</strong></div>
        <div><span>URL 路径</span><strong class="num">{{ routeSummary.paths }}</strong></div>
      </div>

      <div v-if="routeGroups.length" class="route-groups">
        <section v-for="group in routeGroups" :key="group.group" class="route-group">
          <header>
            <strong>{{ group.group }}</strong>
            <span>
              {{ routeSummary?.by_group.find((item) => item.group === group.group)?.paths || 0 }} 路径 /
              {{ group.routes.length }} 端点
            </span>
          </header>
          <div v-for="route in group.routes" :key="`${route.method}-${route.path}`" class="route-line">
            <span class="route-method">{{ route.method }}</span>
            <code>{{ route.path }}</code>
            <span class="online-dot" :class="{ offline: !route.online }">
              {{ route.online ? '在线' : '离线' }}
            </span>
          </div>
        </section>
      </div>

      <div class="page-entry-list">
        <strong>页面入口</strong>
        <div v-for="page in health?.page_routes || []" :key="page.path" class="route-line">
          <span class="route-method">PAGE</span>
          <code>{{ page.path }}</code>
          <span>{{ page.label }}</span>
          <span class="online-dot" :class="{ offline: !page.online }">
            {{ page.online ? '在线' : '未构建' }}
          </span>
        </div>
      </div>
    </section>
  </div>
</template>
