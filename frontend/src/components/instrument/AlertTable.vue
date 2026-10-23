<script setup lang="ts">
import { fmt } from '../../services/format'

defineProps<{
  rows: Array<{
    trade_date: string
    level: string
    signal_type?: string | null
    message: string
    price?: number | null
  }>
}>()
</script>

<template>
  <div v-if="!rows.length" class="empty-state compact">该标的还没有提醒记录。</div>
  <div v-else class="alert-list">
    <div v-for="row in rows.slice(0, 12)" :key="`${row.trade_date}-${row.signal_type}-${row.message}`" class="alert-row">
      <span class="alert-level" :class="`is-${row.level.toLowerCase()}`">{{ row.level }}</span>
      <div>
        <strong>{{ row.signal_type || '提醒' }}</strong>
        <p>{{ row.message }}</p>
        <small class="num">{{ row.trade_date }} · {{ fmt(row.price, 3) }}</small>
      </div>
    </div>
  </div>
</template>
