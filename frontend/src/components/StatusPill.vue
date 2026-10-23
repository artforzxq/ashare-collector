<script setup lang="ts">
withDefaults(
  defineProps<{
    tone?: 'neutral' | 'positive' | 'warning' | 'danger'
  }>(),
  { tone: 'neutral' },
)
</script>

<template>
  <span class="status-pill" :class="`is-${tone}`">
    <slot />
  </span>
</template>
