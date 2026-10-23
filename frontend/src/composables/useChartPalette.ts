import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import type { ChartPalette } from '../charts/options'

export function useChartPalette() {
  const version = ref(0)
  let observer: MutationObserver | null = null

  const palette = computed<ChartPalette>(() => {
    void version.value
    const styles = getComputedStyle(document.documentElement)
    const value = (name: string, fallback: string) =>
      styles.getPropertyValue(name).trim() || fallback
    return {
      up: value('--positive', '#c82b2b'),
      down: value('--negative', '#0b8f68'),
      grid: value('--line', '#e2e8f0'),
      axis: value('--faint', '#94a3b8'),
      text: value('--muted', '#64748b'),
      ma20: '#f2a13b',
      ma60: '#3b82f6',
      ma120: '#8b5cf6',
      support: 'rgba(59,130,246,.12)',
      resistance: 'rgba(226,69,60,.12)',
      range: 'rgba(120,130,150,.12)',
    }
  })

  onMounted(() => {
    observer = new MutationObserver(() => {
      version.value += 1
    })
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
  })

  onBeforeUnmount(() => observer?.disconnect())

  return palette
}
