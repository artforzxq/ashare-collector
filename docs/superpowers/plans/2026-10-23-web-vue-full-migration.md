# Web Vue Full Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-file legacy dashboard with a complete Vue 3 frontend while preserving every existing data view, action and API contract.

**Architecture:** Keep the Python JSON API unchanged. Build a typed Vue client around shared API, formatting, chart and application-state modules, then migrate each legacy view behind `/v2/`. The legacy page remains available at `/` until full parity is verified.

**Tech Stack:** Vue 3, TypeScript, Vite, ECharts 6, Vue Router, Vitest.

**Spec:** The legacy behavior is defined by `collector/web/index.html` and the JSON routes in `collector/server.py`.

## Global Constraints

- The Python backend and existing JSON response shapes must remain compatible.
- `features_daily.state` remains the only state source; the frontend must not recalculate state.
- A-share colors stay red for up and green for down in both themes.
- Opening data uses the existing API only; the journal stays read-only in the browser.
- No CDN is allowed. All frontend assets are bundled locally.
- Every new behavior starts with a failing Vitest test.
- Existing unrelated worktree changes are never staged or rewritten.

---

### Task 1: Shared Client Foundation

**Files:**
- Modify: `frontend/src/services/api.ts`
- Create: `frontend/src/services/format.ts`
- Test: `frontend/src/services/format.test.ts`
- Create: `frontend/src/stores/app.ts`
- Create: `frontend/src/components/EChart.vue`
- Create: `frontend/src/charts/options.ts`
- Test: `frontend/src/charts/options.test.ts`

**Interfaces:**
- Produces: `apiGet<T>(path, signal?)`, `apiPost<T>(path, body)`
- Produces: `fmt(value, digits)`, `signed(value, digits)`, `pct(value)`, `posNeg(value, text)`
- Produces: `useAppStore()` with watchlist, selected code, search, add/remove and core refresh
- Produces: `chartOptionForCandles(payload, flags)`, `chartOptionForIntraday(payload)`

- [ ] **Step 1: Write failing format and chart option tests.**
- [ ] **Step 2: Run `npm.cmd test` and verify missing modules fail.**
- [ ] **Step 3: Implement shared services, store and ECharts wrapper.**
- [ ] **Step 4: Run tests and build.**
- [ ] **Step 5: Commit the shared foundation.**

### Task 2: Global Shell and Watchlist

**Files:**
- Modify: `frontend/src/components/AppShell.vue`
- Modify: `frontend/src/navigation.ts`
- Modify: `frontend/src/styles/main.css`
- Test: `frontend/src/navigation.test.ts`

**Interfaces:**
- Consumes: `useAppStore()`
- Produces: searchable watchlist, add/remove actions, freshness strip, selected-code navigation
- Produces: responsive grouped navigation for all seven legacy work areas

- [ ] **Step 1: Add a failing navigation/domain test for every migrated route.**
- [ ] **Step 2: Run the focused test and verify it fails.**
- [ ] **Step 3: Implement the shell and watchlist interactions.**
- [ ] **Step 4: Run tests and build.**
- [ ] **Step 5: Commit the shell.**

### Task 3: Instrument Workbench

**Files:**
- Replace: `frontend/src/views/PlaceholderView.vue` route for `/instrument`
- Create: `frontend/src/views/InstrumentView.vue`
- Create: `frontend/src/components/instrument/MetricStrip.vue`
- Create: `frontend/src/components/instrument/FactorTable.vue`
- Create: `frontend/src/components/instrument/LevelTable.vue`
- Create: `frontend/src/components/instrument/AlertTable.vue`
- Modify: `frontend/src/router.ts`
- Test: `frontend/src/views/InstrumentView.test.ts`

**Interfaces:**
- Consumes: `/api/kline`, `/api/intraday`, `/api/quotes`, `/api/alerts`, `/api/meta`, `/api/analysis`
- Produces: day/intraday chart switch, range and overlay controls, factor/level/alert panels, AI analysis action

- [ ] **Step 1: Write a failing component test that selects a code and renders daily metrics.**
- [ ] **Step 2: Run it and verify the missing component failure.**
- [ ] **Step 3: Implement the instrument view and subcomponents.**
- [ ] **Step 4: Run tests and build.**
- [ ] **Step 5: Commit the instrument workbench.**

### Task 4: Market Dashboard

**Files:**
- Create: `frontend/src/views/MarketView.vue`
- Create: `frontend/src/components/market/MarketBreadth.vue`
- Create: `frontend/src/components/market/SentimentPanel.vue`
- Create: `frontend/src/components/market/MainlinePanel.vue`
- Create: `frontend/src/components/market/FlowPanel.vue`
- Create: `frontend/src/components/market/RankList.vue`
- Modify: `frontend/src/router.ts`
- Test: `frontend/src/views/MarketView.test.ts`

**Interfaces:**
- Consumes: `/api/market`, `/api/mainline`, `/api/support`
- Produces: breadth statistics, distribution, sentiment stage, mainline candidates, gains/losses, flow chart, support panel

- [ ] **Step 1: Write a failing market render test with a representative payload.**
- [ ] **Step 2: Run it and verify the missing view failure.**
- [ ] **Step 3: Implement the market dashboard.**
- [ ] **Step 4: Run tests and build.**
- [ ] **Step 5: Commit the market dashboard.**

### Task 5: Screening Pool

**Files:**
- Create: `frontend/src/views/ScreenView.vue`
- Create: `frontend/src/components/screen/CandidateTable.vue`
- Create: `frontend/src/components/screen/MemberTable.vue`
- Create: `frontend/src/components/screen/OutcomeTable.vue`
- Modify: `frontend/src/router.ts`
- Test: `frontend/src/views/ScreenView.test.ts`

**Interfaces:**
- Consumes: `/api/pool`
- Produces: candidate ranking, watchlist membership, outcome table and sample warnings

- [ ] **Step 1: Write a failing test for candidate and outcome display.**
- [ ] **Step 2: Run it and verify failure.**
- [ ] **Step 3: Implement the screening pool.**
- [ ] **Step 4: Run tests and build.**
- [ ] **Step 5: Commit the screening pool.**

### Task 6: Factors and Backtest

**Files:**
- Create: `frontend/src/views/FactorsView.vue`
- Create: `frontend/src/views/BacktestView.vue`
- Create: `frontend/src/components/research/StatusGrid.vue`
- Modify: `frontend/src/router.ts`
- Test: `frontend/src/views/FactorsView.test.ts`
- Test: `frontend/src/views/BacktestView.test.ts`

**Interfaces:**
- Consumes: `/api/factors`, `/api/backtest`, `/api/analysis`
- Produces: factor KPIs/table/shadow progress, backtest KPIs/table/turnover groups, analysis actions

- [ ] **Step 1: Write failing tests for factor and backtest summaries.**
- [ ] **Step 2: Run focused tests and verify failure.**
- [ ] **Step 3: Implement both research views.**
- [ ] **Step 4: Run tests and build.**
- [ ] **Step 5: Commit factors and backtest.**

### Task 7: New Stocks, Journal Completion and System Jobs

**Files:**
- Create: `frontend/src/views/NewStocksView.vue`
- Modify: `frontend/src/views/JournalView.vue`
- Create: `frontend/src/views/SystemView.vue`
- Create: `frontend/src/components/system/JobRunner.vue`
- Modify: `frontend/src/router.ts`
- Test: `frontend/src/views/NewStocksView.test.ts`
- Test: `frontend/src/views/JournalView.test.ts`

**Interfaces:**
- Consumes: `/api/newstock`, `/api/journal`, `/api/jobs`, `/api/health`
- Produces: new/recent listings, closed-journal table, unlock/guardrails, runnable jobs and live log

- [ ] **Step 1: Write failing tests for new-stock and journal completion.**
- [ ] **Step 2: Run focused tests and verify failure.**
- [ ] **Step 3: Implement the three pages and job runner.**
- [ ] **Step 4: Run tests and build.**
- [ ] **Step 5: Commit these views.**

### Task 8: Static Integration and Full Verification

**Files:**
- Modify: `collector/server.py`
- Modify: `tests/test_server.py`
- Modify: `frontend/README.md`
- Modify: `README.md`

**Interfaces:**
- Produces: Python serves built `/v2` assets and SPA fallback
- Produces: legacy `/` remains available

- [ ] **Step 1: Write a failing server test for `/v2/` and a built asset route.**
- [ ] **Step 2: Run the focused Python test and verify failure.**
- [ ] **Step 3: Implement static file serving and SPA fallback.**
- [ ] **Step 4: Run frontend tests, frontend build, and affected Python tests.**
- [ ] **Step 5: Commit static integration.**
