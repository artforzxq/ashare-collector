<script setup lang="ts">
import { fmt } from '../../services/format'

defineProps<{
  title: string
  rows?: Array<{ code: string; name: string; pct_chg: number }>
}>()

const emit = defineEmits<{
  select: [code: string]
}>()
</script>

<template>
  <section class="surface-card rank-card">
    <div class="section-heading">
      <div>
        <span class="section-kicker">RANKING</span>
        <h3>{{ title }}</h3>
      </div>
    </div>
    <div v-if="!rows?.length" class="empty-state compact">暂无数据</div>
    <div v-else class="rank-list">
      <button v-for="row in rows" :key="row.code" type="button" @click="emit('select', row.code)">
        <span>
          <strong>{{ row.name }}</strong>
          <small class="num">{{ row.code }}</small>
        </span>
        <b class="num" :class="{ positive: row.pct_chg > 0, negative: row.pct_chg < 0 }">
          {{ row.pct_chg > 0 ? '+' : '' }}{{ fmt(row.pct_chg, 2) }}%
        </b>
      </button>
    </div>
  </section>
</template>
