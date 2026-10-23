<script setup lang="ts">
import { fmt } from '../../services/format'

defineProps<{
  rows: Array<{
    level_type: string
    price_low: number
    price_high: number
    engine?: string | null
  }>
  close?: number | null
}>()

function typeLabel(type: string) {
  return { support: '支撑带', resistance: '阻力带', range: '当前区间', neckline: '颈线' }[type] || type
}
</script>

<template>
  <div v-if="!rows.length" class="empty-state compact">还没有关键带数据。</div>
  <div v-else class="level-list">
    <div v-for="(row, index) in rows" :key="`${row.level_type}-${index}`" class="level-row">
      <span class="level-kind" :class="`is-${row.level_type}`">{{ typeLabel(row.level_type) }}</span>
      <strong class="num">{{ fmt(row.price_low, 3) }}–{{ fmt(row.price_high, 3) }}</strong>
      <span class="level-distance num">
        {{ close ? `${(((row.price_low + row.price_high) / 2 / close - 1) * 100).toFixed(2)}%` : '—' }}
      </span>
      <small>{{ row.engine || '' }}</small>
    </div>
  </div>
</template>
