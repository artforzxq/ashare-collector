import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import JournalView from './views/JournalView.vue'
import OverviewView from './views/OverviewView.vue'
import PlaceholderView from './views/PlaceholderView.vue'

const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/overview' },
  {
    path: '/overview',
    name: 'overview',
    component: OverviewView,
    meta: {
      title: '今日总览',
      eyebrow: 'DECISION DESK',
      description: '从数据健康、观察池和风险状态开始，不再把时间花在翻页上。',
    },
  },
  {
    path: '/instrument',
    name: 'instrument',
    component: PlaceholderView,
    meta: {
      title: '单票工作台',
      eyebrow: 'INSTRUMENT',
      description: '下一阶段接入日 K、分时、关键带、风险和提醒的完整决策链。',
    },
  },
  {
    path: '/market',
    name: 'market',
    component: PlaceholderView,
    meta: {
      title: '市场总览',
      eyebrow: 'MARKET',
      description: '下一阶段接入市场广度、情绪阶段、行业主线和资金去向。',
    },
  },
  {
    path: '/screen',
    name: 'screen',
    component: PlaceholderView,
    meta: {
      title: '筛选池',
      eyebrow: 'RESEARCH',
      description: '下一阶段接入形态命中、候选分层和含成本的历史表现。',
    },
  },
  {
    path: '/factors',
    name: 'factors',
    component: PlaceholderView,
    meta: {
      title: '因子台账',
      eyebrow: 'RESEARCH',
      description: '下一阶段接入因子权重、相关性约束、影子状态和违规告警。',
    },
  },
  {
    path: '/backtest',
    name: 'backtest',
    component: PlaceholderView,
    meta: {
      title: '参数回测',
      eyebrow: 'RESEARCH',
      description: '下一阶段接入参数高原、样本外表现和显著性检验。',
    },
  },
  {
    path: '/new-stocks',
    name: 'new-stocks',
    component: PlaceholderView,
    meta: {
      title: '新股与次新',
      eyebrow: 'RESEARCH',
      description: '下一阶段接入上市初期的价格结构、流动性和风险提示。',
    },
  },
  {
    path: '/journal',
    name: 'journal',
    component: JournalView,
    meta: {
      title: '交易账本',
      eyebrow: 'DECISION',
      description: '阅读未平仓记录、组合在险和纪律统计；写入仍由 CLI 负责。',
    },
  },
  {
    path: '/system',
    name: 'system',
    component: PlaceholderView,
    meta: {
      title: '系统与数据',
      eyebrow: 'SYSTEM',
      description: '下一阶段接入服务健康、同步任务、数据覆盖率和规则版本。',
    },
  },
  { path: '/:pathMatch(.*)*', redirect: '/overview' },
]

export const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})
