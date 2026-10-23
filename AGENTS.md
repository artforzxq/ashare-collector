# AGENTS.md — 动手前先读这一份

A 股个人交易提醒系统（Python + SQLite，**后端**没有 web 框架，只硬依赖 PyYAML）。

> **前端另有一层构建**（2026-10-23 起）：`frontend/` 是 Vue 3 + TypeScript + Vite 工程，
> 构建产物输出到 `collector/web/v2/`，由同一个 Python 服务挂在 `/v2/`。见下面「前端」一节。
> 只跑 Python 部分**不需要** Node，`/` 上的旧单页页面一直可用。

这份文件是**给 AI 助手的项目须知**：只写"不知道就会踩坑"的东西。
完整的设计理由与实测证据在 `README.md`（很长，但每条结论都附了数据）；
数据源的取舍在 `docs/数据源对照-a-stock-data.md`。

> 先跑 `python run.py doctor` —— 它能回答"环境/依赖/数据库/配置/数据源现在什么状态"，
> 比翻文档快。

## 前端（`frontend/`）

两个页面并存，**改前端前先确认改的是哪一个**：

| 路径 | 文件 | 状态 |
| --- | --- | --- |
| `/` | `collector/web/index.html`（单文件，约 2900 行） | 旧版，仍在服务，迁移完成前不动 |
| `/v2/` | `frontend/src/**` → 构建到 `collector/web/v2/` | 新版 Vue，9 个视图 |

- **构建产物不入库**：`collector/web/v2/` 在 `.gitignore` 里。克隆后访问 `/v2/` 会拿到 404
  和一句"先在 frontend 目录运行 npm run build"——**这是预期行为，不是 bug**。
- 构建：`cd frontend && npm install && npm run build`。
  `npm install` 只在首次（或 `package.json` / `package-lock.json` 变化后）需要。
- **`npm.cmd` 只是 Windows 写法**，macOS/Linux 用 `npm`。文档和注释里别只写一种。
- 前端只消费 `collector/server.py` 的 JSON 接口，**不许在浏览器里重算状态**
  （硬约束第 2 条同样适用于前端）；涨红跌绿的配色在明暗两套主题下都要成立。
- 前端自带 Vitest：`cd frontend && npm test`。改 `frontend/src` 至少跑一次；
  只改 Python 时不用跑。

## 硬约束（改代码前必须遵守）

1. **每层只有一个出口值**，层内因子先合成再上行；禁止把多个因子各自送到上层。
2. **状态只算一次**：`features_daily.state` 是唯一真相源，任何模块不许自己重算。
3. **改权重或规则 → 必须升 `project.feature_version`**（`config.yaml`），否则历史信号无法解释。
4. **所有写入按主键 upsert**：任何任务都要能全量重跑而不产生重复。
5. **测试自带输入**：测试不许依赖 `config.yaml` 里的活配置（观察池、阈值都是用户会改的）。
6. **`windows/*.bat` 必须纯 ASCII + CRLF**：cmd.exe 按控制台代码页逐字节读，文件里出现
   UTF-8 中文会让它后面每一行都变成"不是内部或外部命令"。中文路径统一走
   `python run.py open <key>`。`macos/*.command` 用 LF，可以写中文。
7. **只接免费免账号的源**。东财 `push2his` 在本机网络**被切**（`datacenter` 是通的，
   别把"东财整体不可用"当成结论）。
8. 结论要写进代码注释/文档，**带上实测数字**——这个项目的风格是"每条规则都有证据"。
9. **成本只有一处口径**（`collector/costs.py`）：佣金 + 印花 + 过户 + 滑点，默认 0.302%
   往返。回测、复盘、分档、风险层都用它；风险层算期望值必须扣成本，不许再出现
   "回测扣了、提醒复盘没扣、风险层干脆没扣"的三套口径。
10. **提醒的风险参数必须冻结**：`alerts.risk_json` 存发出那一刻的仓位/止损/盈亏比/期望/成本。
    `features_daily` 会按天重算，拿它回看半年前的提醒等于用今天的尺子解释昨天的判断。
11. **规则数量封顶、新增必须换出**：主干规则（形态/筛选条件/风险开关）新增一条，
    必须降级或删除一条同类的。项目有因子权重预算，但规则本身也要有预算，否则一样会膨胀。
12. **行业结论必须过覆盖率门限**：`instruments.industry` 覆盖 < 80% 时不画板块结论、
    不说"哪个行业强"。分类口径写进 `industry_classification`，选定后中途不换；
    概念板块会重复计算，成交占比只能用行业口径。
13. **合成标签先过证据检验再给权限**：情绪阶段、主线候选默认只做影子；
    `run.py evidence` 没给出稳定、样本足够的正向证据前，不进 risk / alerts / 仓位公式。

## 日线 ≠ 结论（最容易搞混的一件事）

| 类别 | 有哪些 | 谁更新 |
|---|---|---|
| **原始数据** | 日线、分时、快照、代码表 | `18-全市场同步`、页面快照 |
| **结论** | 特征（状态/趋势分/量比/ATR）、关键带、风险层、提醒、市场广度、ETF 份额 | **`3-每日任务`** |

只跑同步不跑日终，页面就会"日线是最新的、结论全是横线"——这时用
`python run.py daily --offline`（只重算、不联网）补齐，别在盘中联网跑日终
（会拿到半根日线，而且它会被当成正式数据留在库里）。

`sync` 与 `daily --offline` 都已经自动避开"今天那根未收盘的线"。

## 账本 ≠ 提醒

`alerts` 记录**系统当时说了什么**；`trades` 记录**我实际做了什么、当时怎么想的**。
两者分开，复盘才能回答"我有没有照做"，而不是只回答"系统对不对"。

- 开仓字段（论点、失效条件、入场价、止损价、R）写入后**不可修改**，平仓只补结果；
- 组合在险按未平仓头寸的 R **相加**，同题材不享受分散（3 只同题材 ≈ 1 只的 3 倍敞口）；
- "是否按纪律"与盈亏**分开记**：赚钱但违规也要记违规，否则下个月会用"我赚了"给破例背书。

账本维护流程（2026-10-23 起）：成交当天由用户把真实成交和当时的论点、失效条件发给 AI，
AI 通过 `journal open/close` 录入并回报编号与风险告警；周末导入券商成交 CSV，
用 `broker import/show` 对账，但不反向修改账本；每月至少查看一次纪律违规与扣成本后的净期望。
页面账本保持只读。录入时不得替用户事后编造当时的论点或失效条件；券商流水只校对成交事实。

## 数字分三类

引用任何阈值前先认清它是哪一类：**带出处的实证**（可查、可证伪，注意样本与口径）、
**实践归纳的经验值**（描述通常长什么样，不是定律）、**示意数字**（只建立量级感，不要引用）。
三类混着用，结论会看起来比实际精确。形态结论的样本少于 `screen.min_samples_for_claim`
时，页面和报告会明确标"只能当方向看"。

## 情绪阶段是油门，不是方向盘

`market_breadth.sentiment_stage` 只是**影子标签**，不读进 `risk`、不生成 `alerts`。
它回答"现在该更保守还是更积极"，不回答"下一只龙头是谁"。要给它权限，
先过和因子一样的准入流程：影子运行 → 样本外验证 → 转正。

## 常用命令

| 想干什么 | 命令 |
|---|---|
| 环境/数据源/数据库体检 | `python run.py doctor`（`--sources` 会真取一次数） |
| 日终：抓数 → 特征 → 关键带 → 提醒 | `python run.py daily` |
| **离线重算**（不联网，只用本地日线） | `python run.py daily --offline` |
| 全市场同步（可反复跑、自动续） | `python run.py sync` |
| 全市场筛选 / 观察池候选 | `python run.py screen` / `python run.py candidates` |
| 历史重放（立刻拿到 5/20 日表现） | `python run.py replay --days 240` |
| 分档研究（某个维度有没有用） | `python run.py buckets`，可加 `--field 位置` |
| 单票体检 | `python run.py dig 600519` |
| 交易账本（开仓冻结假设 / 平仓复盘 / 组合在险） | `python run.py journal open ...` · `journal list` · `journal close <id> --exit ...` · `journal risk` |
| 券商成交对账（CSV + FIFO，不改账本） | `python run.py broker import 成交.csv` · `broker show` |
| 行业分类（免费源 / CSV 导入 / 覆盖率） | `python run.py industries --sync` · `industries --import data/industry.csv` |
| 主线识别（价格+资金+扩散+持续性，不出信号） | `python run.py mainline` |
| 胜率校准（P1 净胜率 → risk.win_rate） | `python run.py calibrate`（`--write` 才改配置） |
| 影子标签证据检验（情绪/主线前瞻收益） | `python run.py evidence --save` |
| 情绪阶段（影子，不驱动仓位） | `python run.py sentiment`（`--backfill` 补历史） |
| 周度环境卡 / 变化推送 | `python run.py weekly` · `weekly --push`（没变化不推） |
| 风险层分布 | `python run.py riskstats` |
| 龙虎榜+资金流 / 两融 / 资金去向 | `python run.py flows` / `margin` / `flow` |
| 离线自检（夹具源跑通全链路） | `python run.py selftest` |
| 看盘页面（旧版在 `/`，新版在 `/v2/`） | `python run.py web`（`--no-open` 不弹浏览器） |
| 构建新版前端（首次要先 `npm install`） | `cd frontend && npm install && npm run build` |

启动器：`windows\*.bat`（mac 上是 `macos\*.command`，同编号）。编号 20–28 是最近加的，
含义见 README 的"启动文件清单"。**启动器只有编号成对的两套，没有"构建前端"的入口**——
前端构建目前只能手敲上面那条命令。

## 测试

```bash
python run_tests.py                          # 全量：只报失败 + 一行汇总
python run_tests.py -v                       # 全量：逐条打印
python run_tests.py tests/test_risk.py       # 只跑一个文件
python run_tests.py tests.test_risk.RiskAssessTests   # 只跑一个类
```

默认安静是刻意的：五百多个测试的逐条输出有 700 多行，99% 是 "ok"，
对人和对 AI 助手都是噪声。失败信息一条都不会少。全量约 3 分钟。

改完代码至少跑一次全量；只改文档/启动器时，跑受影响的文件即可
（启动器改动看 `tests/test_open_target.py`）。

前端是**另一套**测试，用 Vitest，不归 `run_tests.py` 管：

```bash
cd frontend && npm test        # 14 个 *.test.ts，jsdom 环境
```

改 `frontend/src/**` 跑这个；只改 Python 不用跑。两套都要跑的情况（改了接口契约）
= `python run_tests.py` + `cd frontend && npm test`。

## 反复踩到的坑（详见 README「踩过的坑」）

- **单位陷阱**：腾讯 K 线成交量，科创板（688/689）是"股"、其余是"手"（一律 ×100 会把
  688 的成交额放大 100 倍）；深交所两融**汇总表是亿元、明细表是万元**；
  金十的单位写在元数据里，用它；写库前验恒等式。
- **半根日线**：盘中的当日 K 线一旦进库就没人再修正（晚上快照不会覆盖非快照来源的行，
  第二天增量也不会回头补）。任何"写到今天"的路径都要先问 `market_time.describe()`。
- **确认 vs 状态**：形态确认必须是"刚发生的那一次穿越"，不能是"现在处在某侧"——
  后者会让三重顶多出十几倍。
- **口径混用**："收盘在区间的位置"和"斐波那契回撤"互补但不等价；回撤要求摆动低点
  **在高点之前**且涨幅够一段（≥10%）。代码里 `range_position` 与 `retrace` 分开。
- **聚合门限**：`snapshot_bars` 只覆盖 `source='snapshot'` 的行，别的来源一律当正式数据
  保护起来——所以写入路径错了，就再也没有第二次机会。
- **东财限流**：`datacenter/push2/...` 共用风控面，请求要串行 + 间隔
  （`sources.min_interval_sec`），被封要停 30–60 分钟。

## 什么时候该沉默

这个系统的默认行为是**不说话**：状态在中性带内不发提醒、数据不可信不发提醒、
结构位太远不建仓。看到"没有提醒"先确认不是漏报，但**不要为了"看起来有输出"去放松阈值**——
每次放松都要有回放数据支撑（`replay` / `buckets` 就是干这个的）。
