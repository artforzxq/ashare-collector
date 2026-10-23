<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RefreshCw } from '@lucide/vue'
import { useRouter } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import StatusPill from '../components/StatusPill.vue'
import CandidateTable from '../components/screen/CandidateTable.vue'
import MemberTable from '../components/screen/MemberTable.vue'
import OutcomeTable from '../components/screen/OutcomeTable.vue'
import { ApiError, apiGet } from '../services/api'
import { fmt } from '../services/format'
import { useAppStore } from '../stores/app'

type PoolPayload = {
  ok?: boolean
  message?: string
  days?: number
  min_hits?: number
  min_avg_amount?: number
  cost?: number
  screen_stale?: boolean
  screen_gap_days?: number
  exit_streak?: number
  candidates?: Array<Record<string, any>>
  members?: Array<Record<string, any>>
  outcomes?: Array<Record<string, any>>
  pattern_stats?: { patterns?: Array<Record<string, any>>; baseline?: string; tests?: number }
  asset_mix?: { score?: number; stance?: string; weights?: Record<string, number> }
}

const router = useRouter()
const app = useAppStore()
const payload = ref<PoolPayload | null>(null)
const loading = ref(false)
const actionMessage = ref('')
const error = ref('')

const candidates = computed(() => payload.value?.candidates || [])
const members = computed(() => payload.value?.members || [])
const outcomes = computed(() => payload.value?.outcomes || [])
const patterns = computed(() => payload.value?.pattern_stats?.patterns || [])

async function loadPool() {
  loading.value = true
  error.value = ''
  try {
    payload.value = await apiGet<PoolPayload>('/pool')
    if (payload.value.ok === false) error.value = payload.value.message || '筛选池读取失败'
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '筛选池读取失败'
  } finally {
    loading.value = false
  }
}

async function addCandidate(code: string) {
  actionMessage.value = ''
  try {
    await app.addInstrument(code)
    actionMessage.value = `${code} 已加入观察池`
    await loadPool()
  } catch (cause) {
    actionMessage.value = cause instanceof Error ? cause.message : '加入失败'
  }
}

async function removeMember(code: string) {
  actionMessage.value = ''
  try {
    await app.removeInstrument(code)
    actionMessage.value = `${code} 已移出观察池`
    await loadPool()
  } catch (cause) {
    actionMessage.value = cause instanceof Error ? cause.message : '移出失败'
  }
}

async function selectInstrument(code: string) {
  app.setSelectedCode(code)
  await router.push('/instrument')
}

onMounted(loadPool)
</script>

<template>
  <div class="view-stack">
    <PageHeader
      eyebrow="SCREEN POOL"
      title="筛选池"
      description="把反复命中的标的、观察池现状和历史表现放在一起。页面只复述后端口径，不自行排序。"
    >
      <template #actions>
        <button class="secondary-button" type="button" :disabled="loading" @click="loadPool">
          <RefreshCw :size="15" :class="{ spinning: loading }" />
          刷新
        </button>
      </template>
    </PageHeader>

    <p v-if="error" class="alert-banner is-danger">{{ error }}</p>
    <p v-if="actionMessage" class="inline-note">{{ actionMessage }}</p>

    <div class="metric-grid">
      <div class="metric-card">
        <span class="metric-label">回看窗口</span>
        <strong class="metric-value num">{{ payload?.days || 0 }}</strong>
        <span class="metric-detail">个筛选日</span>
      </div>
      <div class="metric-card">
        <span class="metric-label">候选</span>
        <strong class="metric-value num">{{ candidates.length }}</strong>
        <span class="metric-detail">至少命中 {{ payload?.min_hits || 0 }} 次</span>
      </div>
      <div class="metric-card">
        <span class="metric-label">观察池</span>
        <strong class="metric-value num">{{ members.length }}</strong>
        <span class="metric-detail">连续未命中 {{ payload?.exit_streak || 0 }} 次提示移出</span>
      </div>
      <div class="metric-card">
        <span class="metric-label">往返成本</span>
        <strong class="metric-value num">{{ fmt(payload?.cost, 3) }}%</strong>
        <span class="metric-detail">净收益率已经扣除</span>
      </div>
    </div>

    <p v-if="payload?.screen_stale" class="alert-banner">
      最近一次筛选距今 {{ payload.screen_gap_days || 0 }} 个交易日，出池提示暂时关闭。
    </p>

    <section class="surface-card">
      <div class="section-heading">
        <div>
          <span class="section-kicker">CANDIDATES</span>
          <h3>候选清单</h3>
        </div>
        <StatusPill>{{ candidates.length }} 只</StatusPill>
      </div>
      <p v-if="payload?.asset_mix" class="inline-note">
        市场层 {{ fmt(payload.asset_mix.score, 2) }}（{{ payload.asset_mix.stance || '—' }}）。
        当前建议配比只影响排序，不放松筛选口径。
      </p>
      <CandidateTable :rows="candidates" @add="addCandidate" @select="selectInstrument" />
    </section>

    <section class="surface-card">
      <div class="section-heading">
        <div>
          <span class="section-kicker">WATCHLIST</span>
          <h3>池内现状</h3>
        </div>
        <StatusPill>{{ members.length }} 只</StatusPill>
      </div>
      <p class="inline-note">
        指数与 ETF 按固定定义配置，不参与形态轮换；持仓去留由仓位决定，不因一次未命中直接提示移出。
      </p>
      <MemberTable :rows="members" @drop="removeMember" @select="selectInstrument" />
    </section>

    <section class="surface-card">
      <div class="section-heading">
        <div>
          <span class="section-kicker">OUTCOMES</span>
          <h3>命中后的实际表现</h3>
        </div>
        <StatusPill>毛 / 净并列</StatusPill>
      </div>
      <OutcomeTable :rows="outcomes" :patterns="patterns" />
      <p class="footnote">
        净收益和净胜率已扣 {{ fmt(payload?.cost, 3) }}% 往返成本；样本不足的形态只适合当方向看。
      </p>
    </section>
  </div>
</template>
