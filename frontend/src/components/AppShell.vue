<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Menu, Moon, Sun, X } from '@lucide/vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'

import { navigationSections } from '../navigation'

const route = useRoute()
const mobileNavigationOpen = ref(false)
const theme = ref<'light' | 'dark'>('light')

const pageTitle = computed(() => String(route.meta.title || 'A 股决策台'))
const pageEyebrow = computed(() => String(route.meta.eyebrow || 'WORKSPACE'))

function applyTheme(next: 'light' | 'dark') {
  theme.value = next
  document.documentElement.dataset.theme = next
  localStorage.setItem('ashare:v2-theme', next)
}

function toggleTheme() {
  applyTheme(theme.value === 'light' ? 'dark' : 'light')
}

onMounted(() => {
  const saved = localStorage.getItem('ashare:v2-theme')
  const preferred = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  applyTheme(saved === 'dark' || saved === 'light' ? saved : preferred)
})

watch(
  () => route.fullPath,
  () => {
    mobileNavigationOpen.value = false
  },
)
</script>

<template>
  <div class="app-shell">
    <header class="mobile-header">
      <button
        class="icon-button"
        type="button"
        aria-label="打开导航"
        :aria-expanded="mobileNavigationOpen"
        @click="mobileNavigationOpen = !mobileNavigationOpen"
      >
        <X v-if="mobileNavigationOpen" :size="18" />
        <Menu v-else :size="18" />
      </button>
      <div>
        <span class="mobile-eyebrow">{{ pageEyebrow }}</span>
        <strong>{{ pageTitle }}</strong>
      </div>
      <button class="icon-button" type="button" aria-label="切换主题" @click="toggleTheme">
        <Sun v-if="theme === 'dark'" :size="18" />
        <Moon v-else :size="18" />
      </button>
    </header>

    <aside class="sidebar" :class="{ 'is-open': mobileNavigationOpen }">
      <RouterLink class="brand" to="/overview" aria-label="返回今日总览">
        <span class="brand-mark">A</span>
        <span class="brand-copy">
          <strong>A 股决策台</strong>
          <small>DECISION WORKSPACE</small>
        </span>
        <span class="version-chip">v2</span>
      </RouterLink>

      <nav class="navigation" aria-label="主导航">
        <section v-for="section in navigationSections" :key="section.label" class="nav-section">
          <h2>{{ section.label }}</h2>
          <RouterLink
            v-for="item in section.items"
            :key="item.to"
            :to="item.to"
            class="nav-item"
            :title="item.hint"
          >
            <component :is="item.icon" :size="17" :stroke-width="1.8" />
            <span>{{ item.label }}</span>
          </RouterLink>
        </section>
      </nav>

      <footer class="sidebar-footer">
        <a href="/" class="legacy-link">打开旧版页面</a>
        <span>新版骨架 · 数据只读</span>
      </footer>
    </aside>

    <button
      v-if="mobileNavigationOpen"
      type="button"
      class="nav-backdrop"
      aria-label="关闭导航"
      @click="mobileNavigationOpen = false"
    />

    <main class="main-content">
      <header class="desktop-header">
        <div>
          <span class="page-eyebrow">{{ pageEyebrow }}</span>
          <h1>{{ pageTitle }}</h1>
        </div>
        <button class="theme-button" type="button" @click="toggleTheme">
          <Sun v-if="theme === 'dark'" :size="16" />
          <Moon v-else :size="16" />
          {{ theme === 'dark' ? '浅色' : '深色' }}
        </button>
      </header>
      <RouterView />
    </main>
  </div>
</template>
