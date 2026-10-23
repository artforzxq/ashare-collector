<script setup lang="ts">
import { fmt, pct } from '../../services/format'

defineProps<{
  rows: Array<Record<string, any>>
  patterns?: Array<Record<string, any>>
}>()

function win(row: Record<string, any>, key: string, done: string) {
  return row[done] ? `${((row[key] / row[done]) * 100).toFixed(1)}%` : '—'
}
</script>

<template>
  <div v-if="!rows.length" class="empty-state compact">还没有信号回填表现。</div>
  <div v-else class="data-table-wrap">
    <table class="data-table">
      <thead>
        <tr>
          <th>形态</th>
          <th class="num">样本</th>
          <th class="num">5 日净</th>
          <th class="num">5 日净胜率</th>
          <th class="num">20 日毛</th>
          <th class="num">20 日净</th>
          <th class="num">20 日净胜率</th>
          <th class="num">按日超额</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.criterion">
          <td>
            <strong>{{ row.criterion }}</strong>
            <small class="cell-subtitle">{{ (patterns || []).find((item) => item.criterion === row.criterion)?.sample_warning || '' }}</small>
          </td>
          <td class="num">{{ row.n }}<small class="cell-subtitle">回填 {{ row.done20 || 0 }}</small></td>
          <td class="num" :class="{ positive: row.avg5_net > 0, negative: row.avg5_net < 0 }">{{ pct(row.avg5_net) }}</td>
          <td class="num">{{ win(row, 'win5_net', 'done5') }}</td>
          <td class="num" :class="{ positive: row.avg20 > 0, negative: row.avg20 < 0 }">{{ pct(row.avg20) }}</td>
          <td class="num" :class="{ positive: row.avg20_net > 0, negative: row.avg20_net < 0 }">{{ pct(row.avg20_net) }}</td>
          <td class="num">{{ win(row, 'win20_net', 'done20') }}</td>
          <td class="num">
            {{ fmt((patterns || []).find((item) => item.criterion === row.criterion)?.excess, 2) }}%
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
