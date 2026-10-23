<script setup lang="ts">
import { ref } from 'vue'

import { fmt, pct } from '../../services/format'

defineProps<{
  rows: Array<Record<string, any>>
}>()

const emit = defineEmits<{
  add: [code: string]
  select: [code: string]
}>()

function amount(value?: number | null) {
  return value == null ? '—' : `${(value / 1e8).toFixed(2)} 亿`
}

const expanded = ref<Set<string>>(new Set())

function toggleWhy(code: string) {
  const next = new Set(expanded.value)
  if (next.has(code)) next.delete(code)
  else next.add(code)
  expanded.value = next
}
</script>

<template>
  <div v-if="!rows.length" class="empty-state compact">
    回看窗口里没有同时满足“池外 + 至少命中两次”的标的。
  </div>
  <div v-else class="data-table-wrap">
    <table class="data-table">
      <thead>
        <tr>
          <th>标的</th>
          <th class="num">命中</th>
          <th>形态</th>
          <th class="num">日均成交额</th>
          <th class="num">20 日超额</th>
          <th class="num">风险层</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="row in rows" :key="row.code">
          <tr>
            <td>
              <button class="table-link" type="button" @click="emit('select', row.code)">
                {{ row.name || row.code }}
              </button>
              <small class="cell-subtitle num">{{ row.code }} {{ row.kind_label ? `· ${row.kind_label}` : '' }}</small>
            </td>
            <td class="num">{{ row.hits }} 次<small class="cell-subtitle">{{ row.days }} 个筛选日</small></td>
            <td>{{ (row.criteria || []).join('、') }}</td>
            <td class="num">
              {{ amount(row.avg_amount_60d) }}
              <small v-if="row.liquid === false" class="cell-subtitle warning-text">低于门槛</small>
            </td>
            <td class="num">
              <span v-if="row.excess_20d == null">待回填</span>
              <span v-else :class="{ positive: row.excess_20d > 0, negative: row.excess_20d < 0 }">
                {{ pct(row.excess_20d) }}
              </span>
              <small class="cell-subtitle">{{ row.outcome_n || 0 }} 个样本</small>
            </td>
            <td class="num">
              <span>{{ row.cap == null ? '未计算' : row.cap > 0 ? `${Math.round(row.cap * 100)}%` : '0 仓' }}</span>
              <small class="cell-subtitle">
                {{ row.stop_pct == null ? '—' : `止损 ${fmt(row.stop_pct, 1)}%` }}
                {{ row.risk_reward == null ? '' : ` · ${fmt(row.risk_reward, 2)}:1` }}
              </small>
            </td>
            <td class="candidate-actions">
              <button class="inline-button" type="button" @click="toggleWhy(row.code)">
                {{ expanded.has(row.code) ? '收起' : '为什么' }}
              </button>
              <button class="inline-button" type="button" @click="emit('add', row.code)">加入观察</button>
            </td>
          </tr>
          <tr v-if="expanded.has(row.code)" class="candidate-why-row">
            <td colspan="7">
              <div class="candidate-why">
                <strong>为什么它在榜上</strong>
                <ul>
                  <li v-for="reason in row.why || ['还没有生成解释']" :key="reason">{{ reason }}</li>
                </ul>
              </div>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
  </div>
</template>
