import type { Component } from 'vue'
import {
  ChartNoAxesCombined,
  Database,
  FlaskConical,
  LayoutDashboard,
  ListFilter,
  NotebookPen,
  Search,
  SlidersHorizontal,
  Sparkles,
} from '@lucide/vue'

export interface NavigationItem {
  label: string
  to: string
  icon: Component
  hint: string
}

export interface NavigationSection {
  label: string
  items: NavigationItem[]
}

export const navigationSections: NavigationSection[] = [
  {
    label: '今日',
    items: [
      {
        label: '总览',
        to: '/overview',
        icon: LayoutDashboard,
        hint: '今天先看什么',
      },
      {
        label: '单票工作台',
        to: '/instrument',
        icon: ChartNoAxesCombined,
        hint: '状态、位置与风险',
      },
      {
        label: '市场总览',
        to: '/market',
        icon: Sparkles,
        hint: '广度、情绪与资金',
      },
    ],
  },
  {
    label: '研究',
    items: [
      {
        label: '筛选池',
        to: '/screen',
        icon: Search,
        hint: '条件命中与候选',
      },
      {
        label: '因子台账',
        to: '/factors',
        icon: SlidersHorizontal,
        hint: '权重与影子因子',
      },
      {
        label: '参数回测',
        to: '/backtest',
        icon: FlaskConical,
        hint: '参数稳定与显著性',
      },
      {
        label: '新股与次新',
        to: '/new-stocks',
        icon: ListFilter,
        hint: '上市初期结构',
      },
    ],
  },
  {
    label: '决策',
    items: [
      {
        label: '交易账本',
        to: '/journal',
        icon: NotebookPen,
        hint: '在险与纪律',
      },
    ],
  },
  {
    label: '系统',
    items: [
      {
        label: '系统与数据',
        to: '/system',
        icon: Database,
        hint: '健康、任务与版本',
      },
    ],
  },
]
