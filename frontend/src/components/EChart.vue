<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { BarChart, CandlestickChart, LineChart } from 'echarts/charts'
import {
  DataZoomComponent,
  GridComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
} from 'echarts/components'
import { init, use } from 'echarts/core'
import type { ECharts, EChartsOption } from 'echarts'
import { CanvasRenderer } from 'echarts/renderers'

use([
  BarChart,
  CandlestickChart,
  LineChart,
  DataZoomComponent,
  GridComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
  CanvasRenderer,
])

const props = withDefaults(
  defineProps<{
    option: EChartsOption
    height?: number
  }>(),
  { height: 430 },
)

const host = ref<HTMLDivElement | null>(null)
let chart: ECharts | null = null
let observer: ResizeObserver | null = null

function render() {
  if (!chart) return
  chart.setOption(props.option, { notMerge: true, lazyUpdate: true })
}

onMounted(() => {
  if (!host.value) return
  chart = init(host.value, undefined, { renderer: 'canvas' })
  render()
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(host.value)
})

watch(
  () => props.option,
  () => render(),
  { deep: true },
)

onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
})
</script>

<template>
  <div ref="host" class="echart-host" :style="{ height: `${height}px` }" />
</template>
