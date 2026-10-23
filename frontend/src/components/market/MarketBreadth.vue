<script setup lang="ts">
import { computed } from 'vue'

import { fmt } from '../../services/format'

const props = defineProps<{
  tradeDate?: string | null
  sample?: number
  breadth?: {
    up: number
    down: number
    flat: number
    limit_up: number
    limit_down: number
    median_pct: number
    total_amount: number
  } | null
  distribution?: Array<{ label: string; count: number }>
  note?: string
}>()

const peak = computed(() =>
  Math.max(...(props.distribution || []).map((item) => item.count), 1),
)
</script>

<template>
  <section class="surface-card">
    <div class="section-heading">
      <div>
        <span class="section-kicker">MARKET BREADTH</span>
        <h3>市场概览</h3>
      </div>
      <span class="num muted-label">{{ tradeDate || '暂无日期' }}</span>
    </div>

    <div v-if="!breadth" class="empty-state compact">
      {{ note || '本地还没有市场广度数据，先完成全市场同步与每日任务。' }}
    </div>
    <template v-else>
      <div class="breadth-grid">
        <div><span>上涨</span><strong class="positive num">{{ breadth.up }}</strong></div>
        <div><span>下跌</span><strong class="negative num">{{ breadth.down }}</strong></div>
        <div><span>平盘</span><strong class="num">{{ breadth.flat }}</strong></div>
        <div><span>涨停</span><strong class="positive num">{{ breadth.limit_up }}</strong></div>
        <div><span>跌停</span><strong class="negative num">{{ breadth.limit_down }}</strong></div>
        <div><span>涨跌中位数</span><strong class="num">{{ fmt(breadth.median_pct, 2) }}%</strong></div>
        <div><span>成交额</span><strong class="num">{{ (breadth.total_amount / 1e8).toFixed(0) }} 亿</strong></div>
      </div>

      <div class="distribution-list">
        <div v-for="row in distribution || []" :key="row.label" class="distribution-row">
          <span>{{ row.label }}</span>
          <i>
            <b
              :style="{ width: `${Math.max(2, (row.count / peak) * 100)}%` }"
              :class="{ falling: row.label.startsWith('-') }"
            />
          </i>
          <strong class="num">{{ row.count }}</strong>
        </div>
      </div>
      <p class="inline-note">
        本地样本 <b class="num">{{ sample || 0 }}</b> 只。样本越全，这组数字越接近真实全市场。
      </p>
    </template>
  </section>
</template>
