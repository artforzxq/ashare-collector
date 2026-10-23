<script setup lang="ts">
import { fmt } from '../../services/format'

defineProps<{
  rows: Array<Record<string, any>>
}>()

const emit = defineEmits<{
  drop: [code: string]
  select: [code: string]
}>()

const stateLabels: Record<string, string> = { up: '上升', down: '下跌', range: '震荡' }
</script>

<template>
  <div v-if="!rows.length" class="empty-state compact">观察池是空的。</div>
  <div v-else class="data-table-wrap">
    <table class="data-table">
      <thead>
        <tr>
          <th>标的</th>
          <th class="num">被筛出</th>
          <th>形态</th>
          <th>状态</th>
          <th class="num">连续未命中</th>
          <th class="num">日均成交额</th>
          <th class="num">风险层</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.code">
          <td>
            <button class="table-link" type="button" @click="emit('select', row.code)">
              {{ row.name || row.code }}
            </button>
            <small class="cell-subtitle num">{{ row.code }} · {{ row.type || '' }}</small>
          </td>
          <td class="num">{{ row.hits || 0 }} 次<small class="cell-subtitle">{{ row.latest?.trade_date || row.last_date || '' }}</small></td>
          <td>{{ (row.criteria || []).join('、') || '最近没出现' }}</td>
          <td>{{ stateLabels[row.state] || row.state || '—' }}</td>
          <td class="num">
            <span :class="{ 'warning-text': row.exit_hint }">
              {{ row.type !== 'stock' ? '不参与' : row.miss_streak ?? '—' }}
            </span>
          </td>
          <td class="num">
            {{ row.avg_amount_60d == null ? '—' : `${(row.avg_amount_60d / 1e8).toFixed(2)} 亿` }}
            <small v-if="row.amount_breach" class="cell-subtitle warning-text">低于门槛</small>
          </td>
          <td class="num">{{ row.cap == null ? '—' : row.cap > 0 ? `${Math.round(row.cap * 100)}%` : '0 仓' }}<small class="cell-subtitle">{{ row.stop_pct == null ? '' : `止损 ${fmt(row.stop_pct, 1)}%` }}</small></td>
          <td><button class="inline-button is-danger" type="button" @click="emit('drop', row.code)">移出</button></td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
