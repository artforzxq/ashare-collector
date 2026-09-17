"""数据字典：为每张表、每个字段提供中文说明。

SQLite 没有 MySQL 那种字段注释，所以这里做三件事：
1. 把说明写进 schema.sql 的注释里（看建表语句时能看到）；
2. 写入 data_dictionary 表（Navicat / DB Browser 里直接当表查）；
3. 导出 字段说明.md（给人读）。

新增字段后如果忘了写说明，build() 会把缺漏列出来，不会静默通过。
"""

from __future__ import annotations

from pathlib import Path

from . import db

TABLE_DOCS: dict[str, str] = {
    "instruments": "标的清单：观察池里的指数、ETF、个股及其角色",
    "trade_calendar": "交易日历：判断某天是否开市、前后交易日",
    "bars_daily": "日线行情：原始价与复权因子分开存，特征只用前复权价",
    "bars_intraday": "分钟线：只保留近 250 个交易日",
    "market_breadth": "市场广度：每天由全市场快照现算，量能与情绪的地基",
    "etf_shares": "ETF 份额：观察疑似托底资金的核心代理指标",
    "margin": "融资融券：T+1 披露，注意 data_date 与 fetched_date 的差别",
    "features_daily": "特征与状态：全部由行情表计算得来，可随时重算（纯函数产物）",
    "levels": "关键点位：成交量密集区聚类出的支撑带与阻力带",
    "alerts": "提醒与复盘：每条提醒的完整上下文与后续表现回填",
    "data_health": "数据体检：每次采集任务的成败、行数、耗时",
    "factor_registry": "指标台账：每个因子的角色、权重、生命周期",
    "factor_contributions": "因子贡献：回答“结论是被谁拉过去的”",
    "arbitration_log": "仲裁日志：记录被压制或降级的信号，以及依据的规则",
    "rule_version": "规则版本：权重或规则每改一次都要留版本",
    "data_dictionary": "数据字典：表与字段的中文说明（本表）",
}

# 字段说明：(中文名/含义, 补充提示)
FIELD_DOCS: dict[str, dict[str, tuple[str, str]]] = {
    "instruments": {
        "code": ("标的代码，带交易所前缀", "如 SH510300、SZ159919、SH000300"),
        "name": ("标的名称", "未接名称源时与代码相同"),
        "type": ("标的类型", "index 指数 / etf 基金 / stock 个股"),
        "exchange": ("交易所", "SH 上交所 / SZ 深交所"),
        "board": ("板块", "主板 / 创业板 / 科创板，个股用"),
        "is_st": ("是否 ST", "1 是；ST 放宽涨跌幅跳变阈值"),
        "is_active": ("是否仍在交易", "0 表示已退市或长期停牌"),
        "listed_date": ("上市日期", "YYYY-MM-DD"),
        "delisted_date": ("退市日期", "未退市为 NULL"),
        "in_watchlist": ("是否在观察池", "1 是"),
        "role": ("角色", "基准 / 观察 / 持仓"),
        "updated_at": ("最后更新时间", "本行写入时间"),
    },
    "trade_calendar": {
        "trade_date": ("日期", "YYYY-MM-DD"),
        "is_trading_day": ("是否交易日", "1 开市，0 休市"),
        "prev_trade_date": ("上一个交易日", "用于取“昨天”的特征行"),
        "next_trade_date": ("下一个交易日", "用于判断提醒的冷却期"),
    },
    "bars_daily": {
        "code": ("标的代码", "带交易所前缀"),
        "trade_date": ("交易日", ""),
        "open": ("开盘价", "原始价，未复权"),
        "high": ("最高价", "原始价"),
        "low": ("最低价", "原始价"),
        "close": ("收盘价", "原始价；展示用，不要拿来算指标"),
        "pre_close": ("前收盘价", "计算涨跌幅用"),
        "volume": ("成交量", "单位：股"),
        "amount": ("成交额", "单位：元；量能类因子用的是它而不是成交量"),
        "turnover_rate": ("换手率", "单位：%"),
        "pct_chg": ("当日涨跌幅", "单位：%"),
        "adj_factor": ("复权因子", "与原始价分开存，换复权方式时不用重抓数据"),
        "close_adj": ("前复权收盘价", "关键：所有特征计算只用这一列"),
        "source": ("数据来源", "adata / akshare / fixture 等"),
        "quality_flag": ("数据质量", "ok 正常 / suspect 双源不一致 / stale 陈旧 / blocked 跳变被阻断"),
        "updated_at": ("最后更新时间", ""),
    },
    "bars_intraday": {
        "code": ("标的代码", ""),
        "dt": ("时间戳", "YYYY-MM-DD HH:MM"),
        "period": ("周期", "单位分钟：5 / 30 / 60"),
        "open": ("区间开盘价", ""),
        "high": ("区间最高价", ""),
        "low": ("区间最低价", ""),
        "close": ("区间收盘价", ""),
        "volume": ("区间成交量", "单位：股"),
        "amount": ("区间成交额", "单位：元"),
        "source": ("数据来源", ""),
    },
    "market_breadth": {
        "trade_date": ("交易日", ""),
        "up_count": ("上涨家数", "由全市场快照现算"),
        "down_count": ("下跌家数", ""),
        "flat_count": ("平盘家数", ""),
        "limit_up_count": ("涨停家数", "按涨幅 ≥ 9.8% 近似，未区分 20% 涨跌幅板块"),
        "limit_down_count": ("跌停家数", "按涨幅 ≤ -9.8% 近似"),
        "broken_limit_count": ("炸板家数", "需连板数据，第二阶段补"),
        "max_boards": ("最高连板高度", "需连板数据，第二阶段补"),
        "up_ratio": ("上涨占比", "上涨 /（上涨 + 下跌）"),
        "median_pct_chg": ("涨跌幅中位数", "单位：%；比指数更能反映个股体感"),
        "total_amount": ("全市场成交额", "单位：元"),
        "sh_amount": ("沪市成交额", "单位：元"),
        "sz_amount": ("深市成交额", "单位：元"),
        "source": ("数据来源", ""),
        "updated_at": ("最后更新时间", ""),
    },
    "etf_shares": {
        "code": ("ETF 代码", ""),
        "trade_date": ("交易日", ""),
        "shares": ("基金总份额", "单位：份；份额抬升 + 成交放大 = 疑似托底"),
        "nav": ("基金净值", ""),
        "close": ("收盘价", "二级市场价"),
        "premium_rate": ("折溢价率", "（收盘价 / 净值 - 1）× 100，单位：%"),
        "assets": ("基金规模", "单位：元"),
        "is_estimated": ("份额是否估算", "1 估算，0 正式披露"),
        "source": ("数据来源", ""),
        "updated_at": ("最后更新时间", ""),
    },
    "margin": {
        "trade_date": ("抓取日", ""),
        "market": ("市场", "SH / SZ"),
        "data_date": ("数据对应日期", "融资余额 T+1 披露，通常比抓取日早一天"),
        "fetched_date": ("实际抓取日期", ""),
        "financing_balance": ("融资余额", "单位：元"),
        "securities_lending": ("融券余额", "单位：元"),
        "total": ("融资融券余额合计", "单位：元"),
        "net_buy": ("融资净买入", "单位：元"),
        "source": ("数据来源", ""),
    },
    "features_daily": {
        "code": ("标的代码", ""),
        "trade_date": ("交易日", ""),
        "ma20": ("20 日均线", "基于前复权收盘价"),
        "ma60": ("60 日均线", ""),
        "ma120": ("120 日均线", ""),
        "ma_slope_20": ("MA20 斜率", "（今 MA20 / 20 日前 MA20 - 1）× 100，单位：%"),
        "ma_align": ("均线排列", "1 多头 / -1 空头 / 0 纠缠"),
        "adx14": ("14 日 ADX", "影子因子，当前只记录不计分"),
        "atr14": ("14 日平均真实波幅", ""),
        "atr_pct": ("ATR 占比", "ATR / 收盘价 × 100，单位：%"),
        "vol_ratio_20": ("量比", "当日成交额 / 前 20 日平均成交额；突破确认要求 ≥ 1.5"),
        "amount_zscore": ("成交额 z 分数", "相对前 20 日，衡量放量程度"),
        "dist_to_high_250": ("距 250 日高点", "单位：%，负值表示低于前高"),
        "donchian_break": ("通道突破", "1 破 20 日高 / -1 破 20 日低 / 0 区间内"),
        "consolidation_days": ("窄幅整理天数", "20 日振幅 ≤ 12% 的连续天数，上限 60"),
        "range_width_pct": ("振幅宽度", "前 20 日振幅 / 收盘价，单位：%"),
        "vol_shrink_ratio": ("量能萎缩比", "近 5 日均量 / 近 60 日均量；≤ 0.6 视为缩量蓄势"),
        "breakout_confirmed": ("放量突破确认", "1 是（突破且量比 ≥ 1.5）"),
        "trend_score": ("趋势分", "0–100，状态层唯一的出口值"),
        "range_score": ("震荡分", "0–100，由趋势分派生：100 - |趋势分 - 50| × 2"),
        "opportunity_score": ("机会分", "0–1，机会层出口值，0.5 为中性"),
        "state": ("市场状态", "up 上升趋势 / range 震荡 / down 下跌趋势"),
        "state_since": ("状态起始日", "含迟滞确认后的实际起点"),
        "state_days": ("状态持续天数", "交易日数"),
        "data_quality_flag": ("数据质量", "非 ok 时冻结当日自动提醒"),
        "feature_version": ("规则版本", "权重或规则改动后必须升版本"),
        "updated_at": ("最后更新时间", ""),
    },
    "levels": {
        "id": ("自增主键", ""),
        "code": ("标的代码", ""),
        "trade_date": ("交易日", ""),
        "level_type": ("点位类型", "support 支撑带 / resistance 阻力带"),
        "price_low": ("带下沿", "支撑阻力按区间存，不是单一价格点"),
        "price_high": ("带上沿", ""),
        "weight": ("成交量权重", "越大说明该价位换手越密集"),
        "engine": ("生成方式", "volume_profile 成交量密集区 / prior_high 前高 / gap 缺口 / ma_cluster 均线簇"),
    },
    "alerts": {
        "id": ("自增主键", ""),
        "created_at": ("生成时间", ""),
        "trade_date": ("所属交易日", "冷却期按它计算"),
        "code": ("标的代码", ""),
        "level": ("提醒级别", "P0 立即处理 / P1 可操作 / P2 信息"),
        "signal_type": ("信号类型", "STATE_TO_UP、BREAKOUT_CONFIRMED、PULLBACK_TO_SUPPORT、STOP_BREACH、DATA_ANOMALY 等"),
        "state": ("生成时状态", "up / range / down"),
        "price": ("触发价格", ""),
        "message": ("人话描述", ""),
        "payload_json": ("附加信息", "JSON，含带区间、量能等上下文"),
        "feature_version": ("规则版本", "该提醒由哪个版本的规则产生"),
        "arbitrated_by": ("放行规则", "如 R6_PASS"),
        "suppressed_by": ("曾压制它的规则", "被降级时记录"),
        "notified_at": ("推送时间", ""),
        "ack_at": ("人工确认时间", ""),
        "outcome_5d": ("5 日后表现", "单位：%；用于统计信号胜率"),
        "outcome_20d": ("20 日后表现", "单位：%"),
    },
    "data_health": {
        "run_date": ("任务运行日", ""),
        "source": ("数据源", ""),
        "task": ("任务名", "如 daily:SH000300、breadth、etf_shares"),
        "status": ("执行结果", "ok / retry / failed / warn"),
        "rows": ("处理记录数", ""),
        "missing_rate": ("缺失率", "0–1"),
        "latency_ms": ("耗时", "单位：毫秒"),
        "error_msg": ("错误或说明", ""),
        "created_at": ("记录时间", ""),
    },
    "factor_registry": {
        "factor_id": ("因子标识", "如 ma_slope、vol_confirm"),
        "name": ("因子中文名", ""),
        "layer": ("所属层", "state 状态层 / opportunity 机会层 / risk 风险层"),
        "role": ("角色", "primary 主干 / modifier 修正 / veto 否决"),
        "category": ("类别", "trend / volume / volatility / structure / fund / sentiment"),
        "weight": ("权重", "同层内归一化到 1；类别权重有预算上限"),
        "min_samples": ("最小样本数", "历史不足则当日不参与打分"),
        "status": ("生命周期", "candidate 候选 / shadow 影子 / active 生效 / retired 退役"),
        "feature_version": ("规则版本", ""),
        "added_date": ("登记日期", ""),
        "retired_date": ("退役日期", ""),
        "retire_reason": ("退役原因", ""),
    },
    "factor_contributions": {
        "trade_date": ("交易日", ""),
        "code": ("标的代码", ""),
        "factor_id": ("因子标识", ""),
        "raw_value": ("因子原始值", "未归一化"),
        "normalized_score": ("归一化分数", "0–1"),
        "weight": ("当日生效权重", "0 表示该因子只记录不计分（影子期）"),
        "contribution": ("贡献分", "权重 × 归一化分数"),
        "feature_version": ("规则版本", ""),
    },
    "arbitration_log": {
        "id": ("自增主键", ""),
        "created_at": ("记录时间", ""),
        "trade_date": ("交易日", ""),
        "code": ("标的代码", ""),
        "conflict_type": ("冲突类型", "通常是信号类型"),
        "party_a": ("冲突一方", "信号"),
        "party_b": ("冲突另一方", "层级，如 风险层"),
        "rule_applied": ("适用规则", "R0 数据健康 / R1 风险否决 / R2 状态优先 / R3 默认沉默 / R4 跨周期 / R5 冷却 / R6 预算"),
        "decision": ("裁决结果", "suppressed 压制 / downgraded 降级"),
        "suppressed_signal": ("被压制的信号", ""),
        "outcome_20d": ("20 日后表现", "用于评估裁决是否正确"),
    },
    "rule_version": {
        "feature_version": ("规则版本号", ""),
        "effective_from": ("生效日期", ""),
        "change_note": ("变更说明", ""),
        "weights_snapshot": ("权重快照", "JSON，记录当期各因子权重与状态"),
    },
    "data_dictionary": {
        "table_name": ("表名", ""),
        "column_name": ("字段名", ""),
        "ordinal": ("字段顺序", "从 0 开始"),
        "data_type": ("字段类型", "SQLite 的亲和类型"),
        "is_pk": ("是否主键", "1 是"),
        "description": ("字段说明", ""),
        "note": ("补充提示", "用法或注意事项"),
    },
}


def build(conn) -> dict:
    """用实际表结构对照文档，写入 data_dictionary，并返回缺漏清单。"""
    tables = [
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    rows = []
    missing: list[str] = []

    for table in tables:
        for column in conn.execute(f"PRAGMA table_info({table})"):
            name = column["name"]
            description, note = FIELD_DOCS.get(table, {}).get(name, ("", ""))
            if not description:
                missing.append(f"{table}.{name}")
                description = "（待补充）"
            rows.append(
                {
                    "table_name": table,
                    "column_name": name,
                    "ordinal": column["cid"],
                    "data_type": column["type"],
                    "is_pk": 1 if column["pk"] else 0,
                    "description": description,
                    "note": note,
                }
            )

    db.upsert_rows(conn, "data_dictionary", rows, ["table_name", "column_name"])
    return {"tables": len(tables), "columns": len(rows), "missing": missing}


def to_markdown() -> str:
    lines = ["# 数据库字段说明", "", f"共 {len(FIELD_DOCS)} 张表。本文档由 `python run.py dictionary` 生成。", ""]
    for table, fields in FIELD_DOCS.items():
        lines.append(f"## {table}")
        lines.append("")
        lines.append(TABLE_DOCS.get(table, ""))
        lines.append("")
        lines.append("| 字段 | 说明 | 备注 |")
        lines.append("|---|---|---|")
        for column, (description, note) in fields.items():
            lines.append(f"| `{column}` | {description} | {note} |")
        lines.append("")
    return "\n".join(lines)


def write_markdown(path: str | Path) -> Path:
    target = Path(path)
    target.write_text(to_markdown(), encoding="utf-8")
    return target
