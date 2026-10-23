<script setup lang="ts">
import { fmt } from '../../services/format'

defineProps<{
  rows: Array<{
    factor_id: string
    name?: string | null
    category?: string | null
    role?: string | null
    status?: string | null
    raw_value?: number | null
    normalized_score?: number | null
    weight?: number | null
    contribution?: number | null
  }>
}>()
</script>

<template>
  <div v-if="!rows.length" class="empty-state compact">该标的还没有因子贡献记录。</div>
  <div v-else class="factor-list">
    <div v-for="row in rows" :key="row.factor_id" class="factor-row">
      <div class="factor-main">
        <strong>{{ row.name || row.factor_id }}</strong>
        <small>{{ row.category || row.role || '—' }}</small>
      </div>
      <span class="factor-score num">{{ fmt(row.normalized_score, 2) }}</span>
      <span class="factor-weight num">权重 {{ fmt(row.weight, 2) }}</span>
      <span
        class="factor-contribution num"
        :class="{ positive: (row.contribution || 0) > 0, negative: (row.contribution || 0) < 0 }"
      >
        {{ fmt(row.contribution, 3) }}
      </span>
    </div>
  </div>
</template>
