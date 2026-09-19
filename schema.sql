-- ============================================================================
-- A 股个人交易提醒系统 · 表结构
-- 主键统一为 (code, 日期)，全部支持按日 upsert 重跑；时间一律 Asia/Shanghai。
-- 字段中文说明以本文件注释为准，同时写入 data_dictionary 表供查询。
-- ============================================================================

-- 日志模式：显式用普通的 rollback 模式（delete），刻意不开 WAL。
-- 原因：WAL 库每次打开（哪怕只是查询）都必须在库文件旁边创建 <库名>-wal / <库名>-shm。
--       库放在 ~/Downloads 这类受保护目录时，沙盒版数据库客户端（如 Navicat）只有单个文件的权限，
--       建不了这两个旁挂文件，就会报 "unable to open database file"（错误 14）。
--       个人单机使用不需要 WAL 的并发写入能力；真要开，把这行换成 WAL，
--       并确保使用方对 data 目录有写权限。
PRAGMA journal_mode = delete;
PRAGMA foreign_keys = ON;

-- ---------- 基础 ----------

CREATE TABLE IF NOT EXISTS instruments (
  code          TEXT PRIMARY KEY,   -- 标的代码，带交易所前缀：SH510300 / SZ159919
  name          TEXT,               -- 标的名称（未接名称源时与代码相同）
  type          TEXT,               -- 类型：index 指数 / etf 基金 / stock 个股
  exchange      TEXT,               -- 交易所：SH 上交所 / SZ 深交所
  board         TEXT,               -- 板块：主板 / 创业板 / 科创板（个股用）
  is_st         INTEGER DEFAULT 0,  -- 是否 ST：1 是，用于放宽涨跌幅跳变阈值
  is_active     INTEGER DEFAULT 1,  -- 是否仍在交易：0 表示已退市或长期停牌
  listed_date   TEXT,               -- 上市日期 YYYY-MM-DD
  delisted_date TEXT,               -- 退市日期，未退市为 NULL
  in_watchlist  INTEGER DEFAULT 0,  -- 是否在观察池：1 是
  role          TEXT,               -- 角色：基准 / 观察 / 持仓
  updated_at    TEXT                -- 本行最后更新时间
);

CREATE TABLE IF NOT EXISTS trade_calendar (
  trade_date      TEXT PRIMARY KEY, -- 日期 YYYY-MM-DD
  is_trading_day  INTEGER NOT NULL, -- 是否交易日：1 是，0 休市
  prev_trade_date TEXT,             -- 上一个交易日
  next_trade_date TEXT              -- 下一个交易日
);

-- ---------- 行情 ----------

CREATE TABLE IF NOT EXISTS bars_daily (
  code          TEXT NOT NULL,      -- 标的代码，带交易所前缀
  trade_date    TEXT NOT NULL,      -- 交易日
  open          REAL,               -- 开盘价（原始价，未复权）
  high          REAL,               -- 最高价
  low           REAL,               -- 最低价
  close         REAL,               -- 收盘价（原始价，不做复权处理）
  pre_close     REAL,               -- 前收盘价
  volume        REAL,               -- 成交量，单位：股
  amount        REAL,               -- 成交额，单位：元
  turnover_rate REAL,               -- 换手率，单位：%
  pct_chg       REAL,               -- 当日涨跌幅，单位：%
  adj_factor    REAL DEFAULT 1.0,   -- 复权因子；与原始价分开存，便于更换复权方式
  close_adj     REAL,               -- 前复权收盘价：所有特征计算只用这一列
  source        TEXT,               -- 数据来源（adata / akshare / fixture …）
  quality_flag  TEXT DEFAULT 'ok',  -- 数据质量：ok 正常 / suspect 双源不一致 / stale 陈旧 / blocked 跳变被阻断
  updated_at    TEXT,               -- 本行最后更新时间
  PRIMARY KEY (code, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_bars_daily_date ON bars_daily (trade_date);

CREATE TABLE IF NOT EXISTS bars_intraday (
  code   TEXT NOT NULL,    -- 标的代码
  dt     TEXT NOT NULL,    -- 时间戳 YYYY-MM-DD HH:MM
  period INTEGER NOT NULL, -- 周期，单位：分钟（5 / 30 / 60）
  open   REAL,             -- 区间开盘价
  high   REAL,             -- 区间最高价
  low    REAL,             -- 区间最低价
  close  REAL,             -- 区间收盘价
  volume REAL,             -- 区间成交量，单位：股
  amount REAL,             -- 区间成交额，单位：元
  source TEXT,             -- 数据来源
  PRIMARY KEY (code, dt, period)
);

-- 龙虎榜：谁上榜、为什么上榜、净买多少（东财）。
-- 同一只票同一天可能因为多条原因上榜，所以"上榜原因"要进主键——两条记录含义完全不同。
CREATE TABLE IF NOT EXISTS lhb (
  trade_date  TEXT NOT NULL,            -- 上榜日
  code        TEXT NOT NULL,            -- 标的代码（带交易所前缀）
  reason      TEXT NOT NULL DEFAULT '', -- 上榜原因
  name        TEXT,                     -- 名称
  close       REAL,                     -- 收盘价
  pct_chg     REAL,                     -- 涨跌幅，单位：%
  net_buy     REAL,                     -- 龙虎榜净买额，单位：元
  buy_amount  REAL,                     -- 买入额，单位：元
  sell_amount REAL,                     -- 卖出额，单位：元
  turnover    REAL,                     -- 龙虎榜成交额，单位：元
  net_ratio   REAL,                     -- 净买额占总成交比，单位：%
  source      TEXT,                     -- 数据来源
  updated_at  TEXT,
  PRIMARY KEY (trade_date, code, reason)
);

CREATE INDEX IF NOT EXISTS idx_lhb_code ON lhb (code, trade_date);

-- 个股资金流：主力/超大单/大单/中单/小单净流入（东财，近约 100 个交易日）。
-- 这是"资金"类因子唯一能拿到历史序列的免费源；实时北向 2024-08 起已停止披露。
CREATE TABLE IF NOT EXISTS fund_flow (
  code        TEXT NOT NULL,   -- 标的代码
  trade_date  TEXT NOT NULL,   -- 交易日
  close       REAL,            -- 收盘价
  pct_chg     REAL,            -- 涨跌幅，单位：%
  main_net    REAL,            -- 主力净流入，单位：元
  main_ratio  REAL,            -- 主力净流入占比，单位：%
  super_net   REAL,            -- 超大单净流入，单位：元
  large_net   REAL,            -- 大单净流入，单位：元
  medium_net  REAL,            -- 中单净流入，单位：元
  small_net   REAL,            -- 小单净流入，单位：元
  source      TEXT,
  updated_at  TEXT,
  PRIMARY KEY (code, trade_date)
);

CREATE TABLE IF NOT EXISTS market_breadth (
  coverage        INTEGER,          -- 参与统计的个股数：样本太少时这一行不能当全市场广度看
  trade_date         TEXT PRIMARY KEY, -- 交易日
  up_count           INTEGER,          -- 上涨家数
  down_count         INTEGER,          -- 下跌家数
  flat_count         INTEGER,          -- 平盘家数
  limit_up_count     INTEGER,          -- 涨停家数（按涨幅 ≥ 9.8% 近似）
  limit_down_count   INTEGER,          -- 跌停家数（按涨幅 ≤ -9.8% 近似）
  broken_limit_count INTEGER,          -- 炸板家数（需连板数据，第二阶段补）
  max_boards         INTEGER,          -- 最高连板高度（需连板数据，第二阶段补）
  up_ratio           REAL,             -- 上涨占比 = 上涨 /（上涨 + 下跌）
  median_pct_chg     REAL,             -- 全市场涨跌幅中位数，单位：%
  total_amount       REAL,             -- 全市场成交额，单位：元
  sh_amount          REAL,             -- 沪市成交额，单位：元
  sz_amount          REAL,             -- 深市成交额，单位：元
  source             TEXT,             -- 数据来源
  updated_at         TEXT              -- 本行最后更新时间
);

CREATE TABLE IF NOT EXISTS etf_shares (
  code         TEXT NOT NULL,       -- ETF 代码
  trade_date   TEXT NOT NULL,       -- 交易日
  shares       REAL,                -- 基金总份额，单位：份（观察"国家队"托底的核心字段）
  nav          REAL,                -- 基金净值
  close        REAL,                -- 二级市场收盘价
  premium_rate REAL,                -- 折溢价率 =（收盘价 / 净值 - 1）× 100，单位：%
  assets       REAL,                -- 基金规模，单位：元
  is_estimated INTEGER DEFAULT 0,   -- 份额是否为估算值：1 估算，0 正式披露
  source       TEXT,                -- 数据来源
  updated_at   TEXT,                -- 本行最后更新时间
  PRIMARY KEY (code, trade_date)
);

CREATE TABLE IF NOT EXISTS margin (
  trade_date         TEXT NOT NULL, -- 抓取日
  market             TEXT NOT NULL, -- 市场：SH 沪市 / SZ 深市
  data_date          TEXT,          -- 数据本身对应的日期（融资余额 T+1 披露，通常早一天）
  fetched_date       TEXT,          -- 实际抓取日期
  financing_balance  REAL,          -- 融资余额，单位：元
  securities_lending REAL,          -- 融券余额，单位：元
  total              REAL,          -- 融资融券余额合计，单位：元
  net_buy            REAL,          -- 当日融资净买入，单位：元
  source             TEXT,          -- 数据来源
  PRIMARY KEY (trade_date, market)
);

-- ---------- 特征与决策 ----------

CREATE TABLE IF NOT EXISTS features_daily (
  avg_amount_20d     REAL,          -- 近 20 个交易日日均成交额（元）：窗口不含当日
  avg_amount_60d     REAL,          -- 近 60 个交易日日均成交额（元）：小票过滤用这一列
  code               TEXT NOT NULL, -- 标的代码
  trade_date         TEXT NOT NULL, -- 交易日
  ma20               REAL,          -- 20 日前复权收盘均线
  ma60               REAL,          -- 60 日均线
  ma120              REAL,          -- 120 日均线
  ma_slope_20        REAL,          -- MA20 斜率 =（今日 MA20 / 20 日前 MA20 - 1）× 100，单位：%
  ma_align           REAL,          -- 均线排列：1 多头（MA20>MA60>MA120）/ -1 空头 / 0 纠缠
  adx14              REAL,          -- 14 日 ADX 趋势强度（影子因子，当前只记录不计分）
  atr14              REAL,          -- 14 日平均真实波幅
  atr_pct            REAL,          -- ATR 占收盘价比例，单位：%
  vol_ratio_20       REAL,          -- 量比 = 当日成交额 / 前 20 日平均成交额
  amount_zscore      REAL,          -- 成交额的 20 日 z 分数（相对前 20 日）
  dist_to_high_250   REAL,          -- 距 250 日最高价的距离，单位：%（负值表示低于前高）
  donchian_break     REAL,          -- 通道突破：1 破前 20 日高 / -1 破前 20 日低 / 0 区间内
  consolidation_days INTEGER,       -- 连续窄幅整理天数（20 日振幅 ≤ 12%，上限 60）
  range_width_pct    REAL,          -- 前 20 日振幅宽度 / 收盘价，单位：%
  vol_shrink_ratio   REAL,          -- 量能萎缩比 = 近 5 日均量 / 近 60 日均量
  breakout_confirmed INTEGER,       -- 放量突破确认：1 是（突破且量比 ≥ 1.5）
  trend_score        REAL,          -- 趋势分 0–100：状态层加权合成后的唯一出口值
  range_score        REAL,          -- 震荡分 0–100，由趋势分派生 = 100 - |趋势分 - 50| × 2
  opportunity_score  REAL,          -- 机会分 0–1：机会层出口值，0.5 为中性
  state              TEXT,          -- 状态：up 上升趋势 / range 震荡 / down 下跌趋势
  state_since        TEXT,          -- 当前状态的起始日期
  state_days         INTEGER,       -- 当前状态已持续交易日数
  data_quality_flag  TEXT DEFAULT 'ok', -- 数据质量，非 ok 时冻结当日自动提醒
  position_cap       REAL,          -- 风险层出口：仓位上限 0–1
  stop_level         REAL,          -- 风险层出口：止损位（结构位优先，其次 N 倍 ATR）
  risk_reward        REAL,          -- 盈亏比：到最近阻力带的空间 ÷ 到止损的空间
  candle_pattern     TEXT,          -- K 线形态摘要：放量长阳 / 阳包阴 / 长下影 等
  risk_note          TEXT,          -- 风险层结论是怎么来的：基准仓位 / 缩放 / 止损依据
  feature_version    TEXT,          -- 规则版本号；权重或规则改动必须升版本
  updated_at         TEXT,          -- 本行最后更新时间
  PRIMARY KEY (code, trade_date)
);

CREATE TABLE IF NOT EXISTS levels (
  id         INTEGER PRIMARY KEY AUTOINCREMENT, -- 自增主键
  code       TEXT NOT NULL,   -- 标的代码
  trade_date TEXT NOT NULL,   -- 交易日
  level_type TEXT NOT NULL,   -- 类型：support 支撑带 / resistance 阻力带
  price_low  REAL NOT NULL,   -- 带下沿
  price_high REAL NOT NULL,   -- 带上沿（支撑阻力按区间而非价格点）
  weight     REAL,            -- 该带的成交量权重，越大越重要
  engine     TEXT,            -- 生成方式：volume_profile / prior_high / gap / ma_cluster
  UNIQUE (code, trade_date, level_type, price_low, price_high)
);

CREATE TABLE IF NOT EXISTS alerts (
  id              INTEGER PRIMARY KEY AUTOINCREMENT, -- 自增主键
  created_at      TEXT NOT NULL, -- 生成时间
  trade_date      TEXT NOT NULL, -- 所属交易日
  code            TEXT NOT NULL, -- 标的代码
  level           TEXT NOT NULL, -- 级别：P0 立即处理 / P1 可操作 / P2 信息
  signal_type     TEXT NOT NULL, -- 信号类型：STATE_TO_UP / BREAKOUT_CONFIRMED / PULLBACK_TO_SUPPORT / STOP_BREACH / DATA_ANOMALY …
  state           TEXT,          -- 生成时的市场状态
  price           REAL,          -- 触发时价格
  message         TEXT,          -- 人话描述
  payload_json    TEXT,          -- 附加信息（带区间、量能等），JSON 字符串
  feature_version TEXT,          -- 生成该提醒所用的规则版本
  arbitrated_by   TEXT,          -- 最终由哪条仲裁规则放行（如 R6_PASS）
  suppressed_by   TEXT,          -- 曾被哪条规则压制或降级
  notified_at     TEXT,          -- 实际推送时间
  ack_at          TEXT,          -- 人工确认时间
  outcome_5d      REAL,          -- 回填：提醒后 5 个交易日涨跌幅，单位：%
  outcome_20d     REAL,          -- 回填：提醒后 20 个交易日涨跌幅，单位：%
  UNIQUE (code, trade_date, signal_type, level)
);

CREATE INDEX IF NOT EXISTS idx_alerts_date ON alerts (trade_date, level);

-- AI 指标分析留痕（百炼 / DashScope）。注意：这张表**只记录**，不参与任何计算——
-- 状态、关键带、仓位上限、提醒都是确定性代码算的，模型的话只当旁注。
-- 存原始 prompt 与回答，是为了事后能回答"这句话当时是根据什么数字说出来的"。
CREATE TABLE IF NOT EXISTS analysis_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT, -- 自增主键
  created_at    TEXT NOT NULL, -- 调用时间
  scope         TEXT,          -- 分析对象：instrument 单只标的 / ledger 因子台账 / backtest 回测结果
  trade_date    TEXT,          -- 分析的交易日
  code          TEXT,          -- 标的代码（台账与回测这两类为 NULL）
  provider      TEXT,          -- 服务商：bailian（阿里云百炼）
  model         TEXT,          -- 模型名，如 qwen-plus
  prompt        TEXT,          -- 实际发出的提示词（含喂进去的全部数字）
  answer        TEXT,          -- 模型回答原文
  prompt_tokens INTEGER,       -- 输入 token 数
  answer_tokens INTEGER,       -- 输出 token 数
  latency_ms    INTEGER,       -- 往返耗时，单位：毫秒
  status        TEXT,          -- ok 成功 / failed 失败
  error_msg     TEXT           -- 失败原因（成功为 NULL）
);

CREATE INDEX IF NOT EXISTS idx_analysis_code ON analysis_log (code, created_at);

-- 新股与次新：单独一张表，因为这些事实（上市多久、几个板）用日线现推很别扭，
-- 而且推送、页面、复盘都要用同一份判定，不该各算各的。
-- 判定依据是**本地日线的第一根**：某个代码只拿到 N 根日线，说明它上市大约 N 个交易日
-- （同步一次要 750 天，拿到的不可能比它上市以来的还多）。
CREATE TABLE IF NOT EXISTS new_listings (
  code            TEXT PRIMARY KEY, -- 标的代码
  name            TEXT,             -- 标的名称
  board           TEXT,             -- 板块：主板 / 创业板 / 科创板 / 北交所
  listed_date     TEXT,             -- 上市日（按本地日线第一根推算）
  trading_days    INTEGER,          -- 上市以来交易日数（本地已有多少根）
  stage           TEXT,             -- new 新股 / recent 次新 / old 已过观察期
  first_close     REAL,             -- 上市第一根日线的收盘
  last_close      REAL,             -- 最新收盘
  since_list_pct  REAL,             -- 上市以来涨跌幅，单位：%
  limit_up_days   INTEGER,          -- 上市以来涨停天数
  boards_from_start INTEGER,        -- 上市最初连续涨停的天数（几个板）
  bars_loaded     INTEGER,          -- 本地已经有多少根日线（0 = 还没同步到它）
  blocked_bars    INTEGER,          -- 其中有几根被数据体检标成 blocked（新股首日常见）
  estimated       INTEGER DEFAULT 0,-- 上市日是不是靠日线推的：1 是推算，0 是权威上市日
  last_seen       TEXT,             -- 最近一次判定的交易日
  updated_at      TEXT              -- 本行最后更新时间
);

CREATE INDEX IF NOT EXISTS idx_new_listings_stage ON new_listings (stage, trading_days);

-- 疑似托底（"国家队"）：宽基 ETF 份额净流入 + 二级市场异常放量，两条同时成立才算。
-- 只写有命中的日子；份额是 T+1 披露，所以这是事后信号，用来次日复盘。
CREATE TABLE IF NOT EXISTS support_days (
  trade_date  TEXT PRIMARY KEY,  -- 交易日
  level       TEXT,              -- 级别：P1 多只齐步 / P2 单只异动
  etf_count   INTEGER,           -- 同时命中的 ETF 只数
  net_inflow  REAL,              -- 命中标的合计净流入，单位：元
  detail_json TEXT,              -- 每只的明细：份额增幅、成交额 z 分数、净流入、用哪个价算的
  created_at  TEXT               -- 记录时间
);

CREATE TABLE IF NOT EXISTS data_health (
  run_date     TEXT NOT NULL, -- 任务运行日
  source       TEXT NOT NULL, -- 数据源名称
  task         TEXT NOT NULL, -- 任务名，如 daily:SH000300
  status       TEXT,          -- 结果：ok 正常 / retry 需重试 / failed 失败 / warn 警告
  rows         INTEGER,       -- 处理记录数
  missing_rate REAL,          -- 缺失率 0–1
  latency_ms   INTEGER,       -- 耗时，单位：毫秒
  error_msg    TEXT,          -- 错误或说明信息
  created_at   TEXT,          -- 记录时间
  PRIMARY KEY (run_date, source, task)
);

-- ---------- 决策审计 ----------

CREATE TABLE IF NOT EXISTS factor_registry (
  factor_id       TEXT PRIMARY KEY, -- 因子标识，如 ma_slope
  name            TEXT,             -- 因子中文名
  layer           TEXT,             -- 所属层：state / opportunity / risk
  role            TEXT,             -- 角色：primary 主干 / modifier 修正 / veto 否决
  category        TEXT,             -- 类别：trend / volume / volatility / structure / fund / sentiment
  weight          REAL,             -- 权重（同层内归一化到 1）
  min_samples     INTEGER,          -- 最小样本数：历史不足则当日不参与打分
  status          TEXT,             -- 生命周期：candidate / shadow / active / retired
  feature_version TEXT,             -- 生效时的规则版本
  added_date      TEXT,             -- 登记日期
  retired_date    TEXT,             -- 退役日期
  retire_reason   TEXT              -- 退役原因
);

CREATE TABLE IF NOT EXISTS factor_contributions (
  trade_date       TEXT NOT NULL, -- 交易日
  code             TEXT NOT NULL, -- 标的代码
  factor_id        TEXT NOT NULL, -- 因子标识
  raw_value        REAL,          -- 因子原始值（未归一化）
  normalized_score REAL,          -- 归一化后的分数 0–1
  weight           REAL,          -- 当日生效权重；0 表示该因子只记录不计分
  contribution     REAL,          -- 贡献分 = 权重 × 归一化分数
  feature_version  TEXT,          -- 规则版本
  PRIMARY KEY (trade_date, code, factor_id)
);

CREATE TABLE IF NOT EXISTS arbitration_log (
  id                INTEGER PRIMARY KEY AUTOINCREMENT, -- 自增主键
  created_at        TEXT NOT NULL, -- 记录时间
  trade_date        TEXT NOT NULL, -- 交易日
  code              TEXT NOT NULL, -- 标的代码
  conflict_type     TEXT NOT NULL, -- 冲突类型，通常是信号类型
  party_a           TEXT,          -- 冲突一方（信号）
  party_b           TEXT,          -- 冲突另一方（层级，如 风险层）
  rule_applied      TEXT,          -- 规则：R0_DATA_HEALTH / R1_RISK_VETO / R2_STATE_PRIORITY / R3_NEUTRAL_SILENCE / R4_CROSS_PERIOD / R5_COOLDOWN / R6_BUDGET
  decision          TEXT,          -- 裁决：suppressed 压制 / downgraded 降级
  suppressed_signal TEXT,          -- 被压制的信号描述
  outcome_20d       REAL           -- 回填：被压制信号的 20 日表现，用于评估裁决是否正确
);

-- 幂等写入用：同一交易日、同一标的、同一信号被同一规则裁决多次，只留一条。
-- 先清掉历史重复（早期版本这里是裸 INSERT，重跑会累积），再建唯一索引。
DELETE FROM arbitration_log
 WHERE id NOT IN (
   SELECT MIN(id) FROM arbitration_log
    GROUP BY trade_date, code, conflict_type, rule_applied, decision
 );

CREATE UNIQUE INDEX IF NOT EXISTS ux_arbitration_log
  ON arbitration_log (trade_date, code, conflict_type, rule_applied, decision);

CREATE TABLE IF NOT EXISTS rule_version (
  feature_version  TEXT PRIMARY KEY, -- 规则版本号
  effective_from   TEXT,             -- 生效日期
  change_note      TEXT,             -- 变更说明
  weights_snapshot TEXT              -- 当期权重快照，JSON 字符串
);

-- ---------- 元数据 ----------

-- ---------- 全市场筛选 ----------

CREATE TABLE IF NOT EXISTS screen_criteria (
  key         TEXT PRIMARY KEY, -- 条件名，如 反转 / 蓄势 / 低位横盘
  title       TEXT,             -- 一句话说明这是找什么
  why         TEXT,             -- 为什么要盯这种形态
  sort_key    TEXT,             -- 按哪个因子排序
  sort_desc   INTEGER,          -- 1 降序 / 0 升序
  conditions  TEXT,             -- 判定条件，JSON（见 collector/rules.py）
  enabled     INTEGER,          -- 是否启用
  updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS screen_results (
  outcome_5d  REAL,             -- 筛选日之后 5 个交易日的真实涨跌（%，次日收盘建仓口径）
  outcome_20d REAL,             -- 之后 20 个交易日；用来回答"筛出来的票后来怎么样"
  trade_date  TEXT NOT NULL,    -- 交易日
  criterion   TEXT NOT NULL,    -- 条件：蓄势 / 突破 / 异动 / 趋势 / 回踩
  rank_no     INTEGER NOT NULL, -- 该条件下的名次
  code        TEXT NOT NULL,    -- 标的代码
  name        TEXT,             -- 标的名称
  close       REAL,             -- 收盘价
  pct_chg     REAL,             -- 当日涨跌幅，单位：%
  state       TEXT,             -- 生成时的状态
  trend_score REAL,             -- 趋势分
  vol_ratio   REAL,             -- 量比（相对 20 日均量）
  detail      TEXT,             -- 命中理由摘要
  created_at  TEXT,             -- 生成时间
  PRIMARY KEY (trade_date, criterion, code)
);

CREATE TABLE IF NOT EXISTS data_dictionary (
  table_name  TEXT NOT NULL, -- 表名
  column_name TEXT NOT NULL, -- 字段名
  ordinal     INTEGER,       -- 字段在表中的顺序
  data_type   TEXT,          -- 字段类型
  is_pk       INTEGER,       -- 是否主键：1 是
  description TEXT,          -- 字段中文说明
  note        TEXT,          -- 补充说明或用法提示
  PRIMARY KEY (table_name, column_name)
);
