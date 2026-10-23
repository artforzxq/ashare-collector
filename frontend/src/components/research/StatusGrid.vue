<script setup lang="ts">
defineProps<{
  items: Array<{
    label: string
    value: string | number
    detail?: string
    tone?: 'neutral' | 'positive' | 'warning' | 'danger'
  }>
}>()
</script>

<template>
  <div class="research-status-grid">
    <div v-for="item in items" :key="item.label" class="research-status" :class="`is-${item.tone || 'neutral'}`">
      <span>{{ item.label }}</span>
      <strong class="num">{{ item.value }}</strong>
      <small>{{ item.detail || '' }}</small>
    </div>
  </div>
</template>
