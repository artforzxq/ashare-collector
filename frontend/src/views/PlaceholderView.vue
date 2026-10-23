<script setup lang="ts">
import { computed } from 'vue'
import { ArrowRight, Layers3 } from '@lucide/vue'
import { useRoute } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'

const route = useRoute()
const eyebrow = computed(() => String(route.meta.eyebrow || 'WORKSPACE'))
const title = computed(() => String(route.meta.title || '待迁移页面'))
const description = computed(() => String(route.meta.description || '这个页面将在下一阶段接入。'))
</script>

<template>
  <div class="view-stack">
    <PageHeader :eyebrow="eyebrow" :title="title" :description="description" />
    <section class="surface-card placeholder-card">
      <span class="placeholder-icon"><Layers3 :size="24" /></span>
      <span class="section-kicker">NEXT ITERATION</span>
      <h3>路由与页面边界已就绪</h3>
      <p>
        当前阶段先固定信息架构、主题、导航和请求层。接入业务时只替换这一页的内容，
        不再改动整套壳层。
      </p>
      <RouterLink class="text-link" to="/journal">
        先看已经接通数据的交易账本
        <ArrowRight :size="15" />
      </RouterLink>
    </section>
  </div>
</template>
