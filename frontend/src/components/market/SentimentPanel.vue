<script setup lang="ts">
import StatusPill from '../StatusPill.vue'

defineProps<{
  sentiment?: {
    sentiment_stage?: string | null
    sentiment_note?: string | null
    max_boards?: number | null
    broken_limit_count?: number | null
    limit_up_next_avg?: number | null
    first_board_next_avg?: number | null
  } | null
}>()
</script>

<template>
  <section class="surface-card">
    <div class="section-heading">
      <div>
        <span class="section-kicker">SENTIMENT</span>
        <h3>情绪阶段</h3>
      </div>
      <StatusPill tone="warning">影子标签</StatusPill>
    </div>
    <div v-if="!sentiment" class="empty-state compact">还没有情绪阶段数据。</div>
    <template v-else>
      <div class="sentiment-stage">
        <strong>{{ sentiment.sentiment_stage || '未定' }}</strong>
        <span>最高 {{ sentiment.max_boards ?? '—' }} 连板 · 炸板 {{ sentiment.broken_limit_count ?? '—' }} 家</span>
      </div>
      <p class="inline-note">{{ sentiment.sentiment_note || '暂时没有可核对的阶段说明。' }}</p>
      <div class="sentiment-premium">
        <span>连板次日溢价 <b class="num">{{ sentiment.limit_up_next_avg == null ? '—' : `${sentiment.limit_up_next_avg.toFixed(2)}%` }}</b></span>
        <span>首板次日溢价 <b class="num">{{ sentiment.first_board_next_avg == null ? '—' : `${sentiment.first_board_next_avg.toFixed(2)}%` }}</b></span>
      </div>
      <p class="footnote">只回答该更保守还是更积极，不生成选股信号。</p>
    </template>
  </section>
</template>
