<script setup lang="ts">
withDefaults(
  defineProps<{
    label: string
    value: string | number
    detail?: string
    tone?: 'neutral' | 'positive' | 'warning' | 'danger'
  }>(),
  {
    detail: '',
    tone: 'neutral',
  },
)
</script>

<template>
  <article class="metric-card" :class="`is-${tone}`">
    <span class="metric-label">{{ label }}</span>
    <strong class="metric-value num">{{ value }}</strong>
    <span v-if="detail" class="metric-detail">{{ detail }}</span>
  </article>
</template>
