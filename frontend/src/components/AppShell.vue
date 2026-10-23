<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Menu, Moon, Plus, Search, Sun, Trash2, X } from '@lucide/vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'

import { navigationSections } from '../navigation'
import { useAppStore } from '../stores/app'

const route = useRoute()
const router = useRouter()
const app = useAppStore()
const mobileNavigationOpen = ref(false)
const theme = ref<'light' | 'dark'>('light')
const query = ref('')
const searching = ref(false)
const watchlistMessage = ref('')

const pageTitle = computed(() => String(route.meta.title || 'A 股决策台'))
const pageEyebrow = computed(() => String(route.meta.eyebrow || 'WORKSPACE'))
const currentCode = computed({
  get: () => app.selectedCode.value,
  set: (code: string) => app.setSelectedCode(code),
})

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
  app.refreshWatchlist().catch(() => undefined)
})

watch(
  () => route.fullPath,
  () => {
    mobileNavigationOpen.value = false
  },
)

async function searchInstruments() {
  searching.value = true
  watchlistMessage.value = ''
  try {
    await app.search(query.value)
  } catch (cause) {
    watchlistMessage.value = cause instanceof Error ? cause.message : '搜索失败'
  } finally {
    searching.value = false
  }
}

async function addSearchResult(code: string) {
  watchlistMessage.value = ''
  try {
    const result = await app.addInstrument(code)
    query.value = ''
    app.clearSearch()
    currentCode.value = result.code || code
    if (route.path !== '/instrument') await router.push('/instrument')
  } catch (cause) {
    watchlistMessage.value = cause instanceof Error ? cause.message : '添加失败'
  }
}

async function selectInstrument(code: string) {
  currentCode.value = code
  if (route.path !== '/instrument') await router.push('/instrument')
}

async function removeInstrument(code: string) {
  watchlistMessage.value = ''
  try {
    await app.removeInstrument(code)
  } catch (cause) {
    watchlistMessage.value = cause instanceof Error ? cause.message : '移除失败'
  }
}

function stateLabel(value?: string | null) {
  return { up: '上升', range: '震荡', down: '下跌' }[value || ''] || '无状态'
}
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

      <section class="watch-panel">
        <form class="watch-search" @submit.prevent="searchInstruments">
          <Search :size="14" />
          <input
            v-model="query"
            type="search"
            placeholder="搜索代码或名称"
            aria-label="搜索股票并加入观察池"
          />
          <button type="submit" :disabled="searching || !query.trim()" aria-label="搜索">
            <Plus :size="14" />
          </button>
        </form>

        <div v-if="app.searchResults.value.length" class="search-results">
          <button
            v-for="item in app.searchResults.value"
            :key="item.code"
            type="button"
            @click="addSearchResult(item.code)"
          >
            <span>{{ item.name }}</span>
            <small class="num">{{ item.code }}</small>
            <Plus :size="13" />
          </button>
        </div>

        <div class="watchlist-heading">
          <span>观察池</span>
          <small>{{ app.items.value.length }} 只</small>
        </div>

        <div class="sidebar-watchlist">
          <button
            v-for="item in app.items.value"
            :key="item.code"
            type="button"
            class="watchlist-row"
            :class="{ 'is-active': currentCode === item.code }"
            @click="selectInstrument(item.code)"
          >
            <span class="watchlist-name">
              <strong>{{ item.name || item.code }}</strong>
              <small class="num">{{ item.code }}</small>
            </span>
            <span
              class="watchlist-change num"
              :class="{ positive: (item.pct_chg || 0) > 0, negative: (item.pct_chg || 0) < 0 }"
            >
              {{ item.pct_chg == null ? '—' : `${item.pct_chg > 0 ? '+' : ''}${item.pct_chg.toFixed(2)}%` }}
            </span>
            <span class="watchlist-state">{{ stateLabel(item.state) }}</span>
            <span
              class="remove-watch"
              role="button"
              tabindex="0"
              aria-label="从观察池移除"
              @click.stop="removeInstrument(item.code)"
              @keydown.enter.stop="removeInstrument(item.code)"
            >
              <Trash2 :size="12" />
            </span>
          </button>
          <p v-if="!app.loading.value && !app.items.value.length" class="watch-empty">
            观察池为空
          </p>
        </div>

        <p v-if="watchlistMessage || app.error.value" class="watch-message">
          {{ watchlistMessage || app.error.value }}
        </p>
        <p v-else-if="app.coverage.value.instruments" class="watch-coverage">
          本地覆盖 <span class="num">{{ app.coverage.value.instruments }}</span> 只标的
        </p>
      </section>

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
        <span>新版前端 · 数据只读</span>
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
