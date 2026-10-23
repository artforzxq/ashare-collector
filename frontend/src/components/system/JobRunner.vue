<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Play, RefreshCw, Terminal } from '@lucide/vue'

import StatusPill from '../StatusPill.vue'
import { ApiError, apiGet, apiPost } from '../../services/api'

type JobRecord = {
  key: string
  title: string
  status: 'running' | 'done' | 'failed'
  started_at?: string
  finished_at?: string
  result?: string
  error?: string
  log?: string[]
}

type JobState = {
  running?: JobRecord | null
  recent?: JobRecord[]
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
let timer: ReturnType<typeof setInterval> | null = null

const current = computed(() => snapshot.value.running || snapshot.value.recent?.[0] || null)
const activeLog = computed(() => current.value?.log || [])

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

    <div class="job-grid">
      <button
        v-for="job in jobs"
        :key="job.key"
        type="button"
        :disabled="Boolean(snapshot.running) || Boolean(startingKey)"
        @click="startJob(job.key)"
      >
        <span class="job-icon"><Play :size="14" /></span>
        <span>
          <strong>{{ job.label }}</strong>
          <small>{{ job.description }}</small>
        </span>
      </button>
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
