<script setup lang="ts">
import { computed } from 'vue'
import type { EChartsOption } from 'echarts'

import EChart from '../EChart.vue'
import StatusPill from '../StatusPill.vue'
import { fmt } from '../../services/format'
import { useChartPalette } from '../../composables/useChartPalette'

const props = defineProps<{
  flow?: {
    regime?: { label?: string; note?: string } | null
    broad_etf?: { net_inflow?: number | null; pct?: number | null } | null
    sector_etf?: { name?: string | null; net_inflow?: number | null } | null
    amount?: { pct?: number | null; amount?: number | null } | null
    margin?: { change_pct?: number | null; balance?: number | null } | null
    history?: Array<{
      trade_date?: string
      broad_etf_inflow?: number | null
      top100_pct?: number | null
    }>
  } | null
}>()

const palette = useChartPalette()
const history = computed(() => props.flow?.history || [])
const option = computed<EChartsOption>(() => ({
  animation: false,
  grid: [
    { left: 40, right: 44, top: 8, height: '42%' },
    { left: 40, right: 44, top: '58%', height: '32%' },
  ],
  tooltip: { trigger: 'axis' },
  xAxis: [
    {
      type: 'category',
      data: history.value.map((item) => item.trade_date || ''),
      axisLabel: { show: false },
      axisLine: { lineStyle: { color: palette.value.grid } },
    },
    {
      type: 'category',
      gridIndex: 1,
      data: history.value.map((item) => item.trade_date || ''),
      axisLabel: { color: palette.value.text, fontSize: 8 },
      axisLine: { lineStyle: { color: palette.value.grid } },
    },
  ],
  yAxis: [
    {
      scale: true,
      axisLabel: { color: palette.value.text, fontSize: 8 },
      splitLine: { lineStyle: { color: palette.value.grid } },
    },
    {
      gridIndex: 1,
      scale: true,
      axisLabel: { color: palette.value.text, fontSize: 8 },
      splitLine: { show: false },
    },
  ],
  series: [
    {
      name: '宽基 ETF 净流入',
      type: 'bar',
      data: history.value.map((item) => ({
        value: item.broad_etf_inflow ?? null,
        itemStyle: {
          color:
            Number(item.broad_etf_inflow || 0) >= 0 ? palette.value.up : palette.value.down,
        },
      })),
    },
    {
      name: 'Top100 成交占比',
      type: 'line',
      xAxisIndex: 1,
      yAxisIndex: 1,
      data: history.value.map((item) => item.top100_pct ?? null),
      showSymbol: false,
      lineStyle: { color: palette.value.ma20, width: 1.5 },
    },
  ],
}))
</script>

<template>
  <section class="surface-card">
    <div class="section-heading">
      <div>
        <span class="section-kicker">MONEY FLOW</span>
        <h3>资金去向</h3>
      </div>
      <StatusPill>{{ flow?.regime?.label || '待判定' }}</StatusPill>
    </div>
    <div v-if="!flow" class="empty-state compact">还没有资金流向数据。</div>
    <template v-else>
      <div class="flow-grid">
        <div><span>宽基 ETF 净流入</span><strong class="num">{{ flow.broad_etf?.net_inflow == null ? '—' : `${(flow.broad_etf.net_inflow / 1e8).toFixed(2)} 亿` }}</strong></div>
        <div><span>行业 ETF</span><strong>{{ flow.sector_etf?.name || '—' }}</strong></div>
        <div><span>成交额变化</span><strong class="num">{{ fmt(flow.amount?.pct, 2) }}%</strong></div>
        <div><span>融资余额变化</span><strong class="num">{{ fmt(flow.margin?.change_pct, 2) }}%</strong></div>
      </div>
      <EChart v-if="history.length" :option="option" :height="190" />
      <p class="footnote">{{ flow.regime?.note || '份额、成交额和两融变化共同描述钱正在往哪里走。' }}</p>
    </template>
  </section>
</template>
