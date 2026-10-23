<script setup lang="ts">
import StatusPill from '../StatusPill.vue'
import { fmt } from '../../services/format'

defineProps<{
  payload?: {
    ok?: boolean
    message?: string
    coverage?: { coverage?: number; industries?: number } | null
    industries?: Array<{
      industry: string
      ret20: number
      share_trend_pp?: number | null
      diffusion: number
      top_weeks?: number
    }>
    mainlines?: Array<{ industry: string }>
    events?: Array<{ industry: string }>
  } | null
}>()
</script>

<template>
  <section class="surface-card">
    <div class="section-heading">
      <div>
        <span class="section-kicker">MAINLINE</span>
        <h3>主线识别</h3>
      </div>
      <StatusPill v-if="payload?.coverage">覆盖 {{ fmt(payload.coverage.coverage, 1) }}%</StatusPill>
    </div>
    <div v-if="!payload?.ok" class="empty-state compact">
      {{ payload?.message || '暂时读不到主线数据。' }}
    </div>
    <template v-else>
      <div class="mainline-list">
        <div v-for="item in (payload.industries || []).slice(0, 4)" :key="item.industry" class="mainline-row">
          <div>
            <strong>{{ item.industry }}</strong>
            <small>在榜 {{ item.top_weeks || 0 }} 周</small>
          </div>
          <span class="num positive">+{{ fmt(item.ret20, 2) }}%</span>
          <span class="num">{{ fmt(item.share_trend_pp, 3) }}pp</span>
          <span class="num">{{ fmt(item.diffusion, 0) }}%</span>
        </div>
      </div>
      <div class="mainline-verdicts">
        <p>
          主线候选：
          <b>{{ (payload.mainlines || []).map((item) => item.industry).join('、') || '暂无' }}</b>
        </p>
        <p>
          事件驱动：
          <span>{{ (payload.events || []).map((item) => item.industry).slice(0, 4).join('、') || '无' }}</span>
        </p>
      </div>
      <p class="footnote">这是“该看哪里”的地图，不是买入信号。</p>
    </template>
  </section>
</template>
