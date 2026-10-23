<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Play, RefreshCw, Terminal } from '@lucide/vue'

import StatusPill from '../StatusPill.vue'
import { ApiError, apiGet, apiPost } from '../../services/api'

type JobRecord = {
  key: string
  title: string
  status: 'running' | 'done' | 'failed' | 'interrupted'
  run_id?: number
  trigger?: string
  scheduled_for?: string | null
  started_at?: string
  finished_at?: string
  result?: string
  error?: string
  log?: string[]
}

type PlanItem = {
  key: string
  title: string
  cadence: 'trading_day' | 'manual'
  after?: string
  depends_on?: string[]
  status: 'done' | 'overdue' | 'pending' | 'blocked' | 'manual'
  expected_for?: string | null
  last_success?: JobRecord | null
  last_failure?: JobRecord | null
  last_run?: JobRecord | null
  data_asof?: string | null
  success_evidence?: 'job_run' | 'data' | null
}

type ScheduleItem = {
  date: string
  key: string
  title: string
  expected_for: string
  status: 'done' | 'overdue' | 'pending' | 'blocked'
  run_id?: number | null
  finished_at?: string | null
  data_asof?: string | null
}

type JobState = {
  running?: JobRecord | null
  recent?: JobRecord[]
  plan?: PlanItem[]
  schedule?: ScheduleItem[]
  summary?: Record<string, number>
  reference_day?: string
  server_now?: string
}

const jobs = [
  { key: 'sync', label: '同步全市场', description: '可反复运行，每次补一批' },
  { key: 'daily', label: '每日任务', description: '抓数、特征、关键带与提醒' },
  { key: 'screen', label: '全市场筛选', description: '更新形态筛选结果' },
  { key: 'snapshot', label: '补当日快照', description: '写入当日快照 K 线' },
  { key: 'intraday', label: '抓当日分时', description: '补齐分时数据' },
  { key: 'backtest', label: '参数回测', description: '运行网格与换手率研究' },
  { key: 'listings', label: '刷新新股判定', description: '更新新股与次新阶段' },
  { key: 'push', label: '推送简报', description: '推送到已配置渠道' },
]

const snapshot = ref<JobState>({ running: null, recent: [] })
const loading = ref(false)
const startingKey = ref('')
const message = ref('')
const filter = ref<'all' | 'attention'>('all')
let timer: ReturnType<typeof setInterval> | null = null

const current = computed(() => snapshot.value.running || snapshot.value.recent?.[0] || null)
const activeLog = computed(() => current.value?.log || [])
const planByKey = computed(() =>
  Object.fromEntries((snapshot.value.plan || []).map((item) => [item.key, item])),
)
const cards = computed(() =>
  jobs.map((job) => ({
    ...job,
    plan: planByKey.value[job.key] || null,
  })),
)
const visibleCards = computed(() => {
  if (filter.value === 'all') return cards.value
  return cards.value.filter((job) => {
    const status = job.plan?.status || 'manual'
    return ['overdue', 'blocked'].includes(status)
      || Boolean(job.plan?.last_failure && status !== 'done')
  })
})
const summary = computed(() => ({
  overdue: snapshot.value.summary?.overdue || 0,
  pending: snapshot.value.summary?.pending || 0,
  done: snapshot.value.summary?.done || 0,
  blocked: snapshot.value.summary?.blocked || 0,
  failed: snapshot.value.summary?.failed || 0,
  manual: snapshot.value.summary?.manual || 0,
}))

function statusText(status?: string) {
  return {
    done: '成功',
    overdue: '应执行未执行',
    pending: '待执行',
    blocked: '依赖未完成',
    manual: '按需',
    running: '运行中',
    failed: '失败',
    interrupted: '中断',
  }[status || 'manual'] || status || '—'
}

function statusTone(status?: string): 'positive' | 'warning' | 'danger' | 'neutral' {
  if (status === 'done') return 'positive'
  if (status === 'overdue' || status === 'failed' || status === 'interrupted') return 'danger'
  if (status === 'pending' || status === 'running') return 'warning'
  return 'neutral'
}

function shortTime(value?: string | null) {
  if (!value) return '—'
  return value.replace(/^20\d{2}-/, '').replace(':00$', '')
}

function lastSuccess(item?: PlanItem | null) {
  return item?.last_success?.finished_at || item?.last_success?.started_at || '—'
}

async function loadState() {
  try {
    snapshot.value = await apiGet<JobState>('/jobs')
  } catch (cause) {
    message.value = cause instanceof ApiError ? cause.message : '任务状态读取失败'
  }
}

async function startJob(key: string) {
  if (snapshot.value.running) return
  startingKey.value = key
  message.value = ''
  try {
    const result = await apiPost<{ ok?: boolean; message?: string }>('/jobs', { job: key })
    if (result.ok === false) throw new Error(result.message || '任务启动失败')
    await loadState()
  } catch (cause) {
    message.value = cause instanceof Error ? cause.message : '任务启动失败'
  } finally {
    startingKey.value = ''
  }
}

async function refresh() {
  loading.value = true
  await loadState()
  loading.value = false
}

onMounted(async () => {
  await loadState()
  timer = setInterval(() => {
    if (snapshot.value.running) void loadState()
  }, 1500)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <section class="surface-card">
    <div class="section-heading">
      <div>
        <span class="section-kicker">JOB CENTER</span>
        <h3>后台任务</h3>
      </div>
      <button class="secondary-button" type="button" :disabled="loading" @click="refresh">
        <RefreshCw :size="14" :class="{ spinning: loading }" />
        刷新状态
      </button>
    </div>

    <p v-if="message" class="inline-note is-warning">{{ message }}</p>
    <p v-if="snapshot.running" class="inline-note">
      <b>{{ snapshot.running.title }}</b> 正在运行，同一时刻只允许一个任务。
    </p>

    <div class="job-summary">
      <button type="button" class="summary-chip is-danger" :class="{ active: filter === 'attention' }" @click="filter = 'attention'">
        应执行未执行 <b>{{ summary.overdue }}</b>
      </button>
      <span class="summary-chip is-warning">待执行 <b>{{ summary.pending }}</b></span>
      <span class="summary-chip is-positive">今日成功 <b>{{ summary.done }}</b></span>
      <span class="summary-chip">依赖阻塞 <b>{{ summary.blocked }}</b></span>
      <span class="summary-chip">失败 <b>{{ summary.failed }}</b></span>
      <span class="summary-chip">按需 <b>{{ summary.manual }}</b></span>
      <button type="button" class="summary-chip" :class="{ active: filter === 'all' }" @click="filter = 'all'">
        全部任务
      </button>
      <span v-if="snapshot.server_now" class="job-clock">服务时间 {{ snapshot.server_now }}</span>
    </div>

    <div class="job-grid">
      <button
        v-for="job in visibleCards"
        :key="job.key"
        type="button"
        :disabled="Boolean(snapshot.running) || Boolean(startingKey)"
        :class="{
          'is-overdue': job.plan?.status === 'overdue',
          'is-blocked': job.plan?.status === 'blocked',
        }"
        @click="startJob(job.key)"
      >
        <span class="job-icon"><Play :size="14" /></span>
        <span class="job-card-body">
          <span class="job-card-top">
            <strong>{{ job.label }}</strong>
            <StatusPill :tone="statusTone(job.plan?.status)">
              {{ statusText(job.plan?.status) }}
            </StatusPill>
          </span>
          <small>{{ job.description }}</small>
          <span class="job-card-meta">
            上次成功 {{ shortTime(lastSuccess(job.plan)) }}
            <template v-if="job.plan?.data_asof"> · 数据 {{ job.plan.data_asof }}</template>
          </span>
          <span v-if="job.plan?.expected_for" class="job-card-meta">
            应执行 {{ shortTime(job.plan.expected_for) }}
            <template v-if="job.plan.depends_on?.length"> · 依赖 {{ job.plan.depends_on.join('、') }}</template>
          </span>
          <span v-if="job.plan?.success_evidence === 'data'" class="job-card-meta">
            成功依据：数据已更新（CLI/启动器执行）
          </span>
          <span v-if="job.plan?.last_failure && job.plan?.status !== 'done'" class="job-card-error">
            上次失败 {{ shortTime(job.plan.last_failure.finished_at) }} · {{ job.plan.last_failure.error || '查看日志' }}
          </span>
        </span>
      </button>
    </div>

    <div v-if="(snapshot.schedule || []).length" class="job-schedule">
      <div class="section-heading compact-heading">
        <div>
          <span class="section-kicker">LAST 7 TRADING DAYS</span>
          <h3>近期计划与结果</h3>
        </div>
        <span class="muted-label">基准日 {{ snapshot.reference_day || '—' }}</span>
      </div>
      <div class="data-table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>日期</th><th>任务</th><th>应执行</th><th>状态</th><th>实际完成</th><th>数据截至</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in snapshot.schedule" :key="`${item.date}-${item.key}`">
              <td class="num">{{ item.date }}</td>
              <td>{{ item.title }}</td>
              <td class="num">{{ shortTime(item.expected_for) }}</td>
              <td>
                <StatusPill :tone="statusTone(item.status)">{{ statusText(item.status) }}</StatusPill>
              </td>
              <td class="num">{{ item.finished_at ? shortTime(item.finished_at) : '—' }}</td>
              <td class="num">{{ item.data_asof || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div v-if="current" class="job-current">
      <div class="section-heading compact-heading">
        <div>
          <span class="section-kicker">LATEST RUN</span>
          <h3>{{ current.title }}</h3>
        </div>
        <StatusPill :tone="current.status === 'running' ? 'warning' : current.status === 'done' ? 'positive' : 'danger'">
          {{ current.status === 'running' ? '运行中' : current.status === 'done' ? '完成' : '失败' }}
        </StatusPill>
      </div>
      <p class="inline-note">{{ current.result || current.error || '暂无结果摘要' }}</p>
      <pre v-if="activeLog.length" class="job-log"><Terminal :size="13" />{{ activeLog.join('\n') }}</pre>
    </div>
  </section>
</template>
