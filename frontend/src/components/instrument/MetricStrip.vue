<script setup lang="ts">
import { computed } from 'vue'

import { fmt } from '../../services/format'

const props = defineProps<{
  latest: Record<string, unknown> | null
}>()

const stateLabels: Record<string, string> = {
  up: '上升趋势',
  range: '震荡',
  down: '下跌趋势',
}

const state = computed(() => String(props.latest?.state || ''))
const stateLabel = computed(() => stateLabels[state.value] || '—')
const positionCap = computed(() => {
  const value = Number(props.latest?.position_cap)
  if (!Number.isFinite(value)) return '—'
  return value > 0 ? `${Math.round(value * 100)}%` : '0（不建仓）'
})
const stopLevel = computed(() => {
  const stop = Number(props.latest?.stop_level)
  if (!Number.isFinite(stop)) return '—'
  const rr = Number(props.latest?.risk_reward)
  return Number.isFinite(rr) ? `${fmt(stop, 3)}（${rr.toFixed(2)} : 1）` : fmt(stop, 3)
})
</script>

<template>
  <div class="instrument-metrics">
    <div class="instrument-metric">
      <span>状态</span>
      <strong :class="`tone-${state || 'neutral'}`">{{ stateLabel }}</strong>
    </div>
    <div class="instrument-metric">
      <span>趋势分</span>
      <strong class="num">{{ fmt(latest?.trend_score as number, 1) }}</strong>
    </div>
    <div class="instrument-metric">
      <span>机会分</span>
      <strong class="num">{{ fmt(latest?.opportunity_score as number, 2) }}</strong>
    </div>
    <div class="instrument-metric">
      <span>量比</span>
      <strong class="num">{{ fmt(latest?.vol_ratio_20 as number, 2) }}</strong>
    </div>
    <div class="instrument-metric">
      <span>仓位上限</span>
      <strong class="num">{{ positionCap }}</strong>
    </div>
    <div class="instrument-metric">
      <span>止损</span>
      <strong class="num">{{ stopLevel }}</strong>
    </div>
    <div class="instrument-metric">
      <span>形态</span>
      <strong>{{ latest?.candle_pattern || '—' }}</strong>
    </div>
  </div>

  <div v-if="latest?.risk_note" class="inline-note">
    风险层结论：{{ latest.risk_note }}
  </div>
</template>
