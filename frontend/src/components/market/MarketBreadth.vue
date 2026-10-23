<script setup lang="ts">
import { computed, ref, watch } from 'vue'

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
  distribution?: Array<{ label: string; count: number; direction?: 'down' | 'flat' | 'up' }>
  note?: string
  amountStats?: {
    value: number
    coverage: number
    prev_date?: string
    prev_value?: number
    prev_coverage?: number
    d1?: {
      ratio: number
      change_pct: number
      comparable?: boolean
      label?: string
    }
    default_window?: number
    windows?: Record<string, {
      days: number
      basis?: string
      ratio?: number
      change_pct?: number
      percentile?: number | null
      label?: string
      comparable: boolean
      reason?: string
    }>
    history?: Array<{ trade_date: string; total_amount: number; coverage?: number | null }>
  } | null
}>()

const peak = computed(() =>
  Math.max(...(props.distribution || []).map((item) => item.count), 1),
)

const selectedWindow = ref(20)
watch(
  () => props.amountStats?.default_window,
  (value) => {
    if (value) selectedWindow.value = value
  },
  { immediate: true },
)

const windows = computed(() =>
  Object.entries(props.amountStats?.windows || {})
    .map(([key, value]) => ({ key: Number(key), ...value }))
    .filter((item) => item.key > 1)
    .sort((left, right) => left.key - right.key),
)
const currentWindow = computed(() =>
  props.amountStats?.windows?.[String(selectedWindow.value)] || null,
)
const fiveDay = computed(() => props.amountStats?.windows?.['5'] || null)

function amount(value?: number | null) {
  if (value == null) return '—'
  return value >= 1e12
    ? `${(value / 1e12).toFixed(2)} 万亿`
    : `${(value / 1e8).toFixed(0)} 亿`
}

function signedPct(value?: number | null) {
  if (value == null) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function sparkline() {
  const points = (props.amountStats?.history || []).slice(-60)
    .map((item) => Number(item.total_amount || 0))
  if (points.length < 2) return ''
  const min = Math.min(...points)
  const max = Math.max(...points)
  const span = Math.max(max - min, 1)
  return points
    .map((value, index) => {
      const x = (index / (points.length - 1)) * 100
      const y = 28 - ((value - min) / span) * 24
      return `${x.toFixed(2)},${y.toFixed(2)}`
    })
    .join(' ')
}

function direction(row: { label: string; direction?: 'down' | 'flat' | 'up' }) {
  if (row.direction) return row.direction
  if (row.label.startsWith('跌') || row.label.startsWith('-')) return 'down'
  if (row.label.startsWith('涨') || row.label.startsWith('+')) return 'up'
  return 'flat'
}

function barStyle(row: { label: string; count: number; direction?: 'down' | 'flat' | 'up' }) {
  const kind = direction(row)
  const width = `${Math.max(0.8, Math.min(1, row.count / peak.value) * 50)}%`
  if (kind === 'down') return { width, right: '50%' }
  if (kind === 'up') return { width, left: '50%' }
  return { width: '2px', left: 'calc(50% - 1px)' }
}
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
        <div>
          <span>成交额</span>
          <strong class="num">{{ amount(amountStats?.value || breadth.total_amount) }}</strong>
          <small v-if="amountStats?.d1" class="volume-secondary">
            较昨日 {{ signedPct(amountStats.d1.change_pct) }}
          </small>
        </div>
      </div>

      <div v-if="amountStats" class="amount-compare">
        <div class="amount-lines">
          <span class="volume-chip">
            较昨日 {{ signedPct(amountStats.d1?.change_pct) }}
            <b>{{ amountStats.d1?.label || (amountStats.d1?.comparable === false ? '不可比' : '—') }}</b>
          </span>
          <span class="volume-chip">
            较5日均 {{ signedPct(fiveDay?.change_pct) }}
            <b>{{ fiveDay?.comparable ? fiveDay?.label : '不可比' }}</b>
          </span>
          <span class="volume-chip is-primary">
            {{ selectedWindow }}日{{ currentWindow?.basis || '中位数' }}
            {{ currentWindow?.ratio == null ? '—' : `${currentWindow.ratio.toFixed(2)}×` }}
            <b v-if="currentWindow?.percentile != null">P{{ currentWindow.percentile.toFixed(0) }} · {{ currentWindow.label }}</b>
            <b v-else>{{ currentWindow?.comparable ? currentWindow?.label : '不可比' }}</b>
          </span>
        </div>

        <div class="amount-window-row">
          <div class="segmented-control">
            <button
              v-for="item in windows"
              :key="item.key"
              type="button"
              :class="{ active: selectedWindow === item.key }"
              @click="selectedWindow = item.key"
            >
              {{ item.key }}日
            </button>
          </div>
          <span class="amount-coverage">
            覆盖 {{ amountStats.coverage }} 只
            <template v-if="amountStats.prev_coverage != null">
              · 前日 {{ amountStats.prev_coverage }} 只
            </template>
          </span>
        </div>

        <p v-if="currentWindow && !currentWindow.comparable" class="inline-note is-warning">
          {{ selectedWindow }}日不可比：{{ currentWindow.reason }}
        </p>
        <p v-else-if="amountStats.d1 && amountStats.d1.comparable === false" class="inline-note is-warning">
          昨日不可比：{{ amountStats.windows?.['1']?.reason || '覆盖或基期数据不足' }}
        </p>

        <svg
          v-if="sparkline()"
          class="amount-spark"
          viewBox="0 0 100 32"
          preserveAspectRatio="none"
          role="img"
          aria-label="最近成交额趋势"
        >
          <polyline :points="sparkline()" fill="none" stroke="currentColor" stroke-width="1.4" />
        </svg>
        <div v-if="amountStats.history?.length" class="amount-spark-labels">
          <span>{{ amountStats.history[0]?.trade_date }}</span>
          <span>最近 {{ Math.min(60, amountStats.history.length) }} 个交易日 · 量能颜色不代表涨跌</span>
          <span>{{ amountStats.history[amountStats.history.length - 1]?.trade_date }}</span>
        </div>
        <p class="inline-note">
          分位 = 今日成交额在所选窗口历史值中的位置；量能档位只做观察，不参与仓位打分。
        </p>
      </div>

      <div class="distribution-list">
        <div v-for="row in distribution || []" :key="row.label" class="distribution-row">
          <span>{{ row.label }}</span>
          <i>
            <b
              :style="barStyle(row)"
              :class="direction(row)"
            />
          </i>
          <strong class="num">{{ row.count }}</strong>
        </div>
      </div>
      <div class="distribution-legend">
        <span class="legend-down">绿色 · 下跌</span>
        <span class="legend-flat">灰色 · 平盘</span>
        <span class="legend-up">红色 · 上涨</span>
        <span>零轴居中</span>
      </div>
      <p class="inline-note">
        本地样本 <b class="num">{{ sample || 0 }}</b> 只。样本越全，这组数字越接近真实全市场。
      </p>
    </template>
  </section>
</template>
