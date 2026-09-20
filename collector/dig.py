"""单票体检：把一只票在系统里所有的"说法"按层摆到一张纸上。

为什么要有这个：平时看一只票要在页面和数据之间来回翻——状态在一个地方、
关键带在另一个地方、筛选命中在第三个地方，量能位置这些还得自己算。
这里只做一件事：**把各个层已经算好的结论按决策链路的顺序读出来**，
不重新发明任何判断（所有结论都来自各自的层），最后再加上一段
"它在全市场分档里落在哪一格、那一格历史表现如何"。

刻意不做的事：不给买卖建议、不算新指标、不做预测。它是一张**体检单**，
不是诊断书——诊断（要不要动手）仍然是人做。
"""

from __future__ import annotations

from . import candles as candles_mod, db, features as features_mod, levels as levels_mod
from . import risk as risk_mod
from .config import watchlist_codes
from .names import display_name
from .registry import FactorRegistry

DEFAULT_SCREEN_LOOKBACK = 20          # 往回看多少个筛选日
DEFAULT_ALERTS = 8                    # 列最近的几条提醒


def _bars(conn, code: str) -> list[dict]:
    return [dict(row) for row in db.query(
        conn,
        """SELECT * FROM bars_daily WHERE code=? AND COALESCE(quality_flag,'ok')!='blocked'
           ORDER BY trade_date""",
        (code,),
    )]


def _feature_row(conn, cfg: dict, code: str, bars: list[dict]) -> tuple[dict | None, bool]:
    """优先用日终算好的特征；池外的票没有就就地算一遍（和候选清单同一条路）。

    返回 (特征行, 是否现算的)。
    """
    latest = bars[-1]["trade_date"]
    stored = db.query_one(
        conn, "SELECT * FROM features_daily WHERE code=? AND trade_date=?", (code, latest))
    if stored:
        return dict(stored), False
    if len(bars) < 30:
        return None, False
    registry = FactorRegistry(cfg.get("factors", []), "dig")
    try:
        series = features_mod.compute_feature_series(bars, cfg, registry, {})
    except Exception:
        return None, False
    return (series[-1] if series else None), True


def _bands(conn, cfg: dict, code: str, bars: list[dict]) -> list[dict]:
    """关键带：优先读日终写好的，没有就现算（含颈线）。"""
    latest = bars[-1]["trade_date"]
    stored = [dict(row) for row in db.query(
        conn,
        """SELECT level_type, price_low, price_high, weight, engine FROM levels
           WHERE code=? AND trade_date=?""",
        (code, latest))]
    if stored:
        return stored
    close = bars[-1]["close"]
    bands = levels_mod.build_levels(bars, close, cfg)
    neckline = levels_mod.neckline_band(bars, cfg=cfg)
    return bands + ([neckline] if neckline else [])


def collect(conn, cfg: dict, code: str) -> dict:
    """把这只票在各层里的当前状态收成一个字典。"""
    # 代码归一化借用页面的那一个：600519 / sh600519 / 600519.SH 都认。
    # 函数内导入是刻意的——dig 是纯分析模块，不该在模块加载时拉进整个 web 服务。
    from .server import normalize_code

    code = normalize_code(code) or (code or "").strip().upper()
    bars = _bars(conn, code)
    if not bars:
        return {"ok": False, "message": f"{code} 在本地没有日线。先 python run.py add {code}，或者跑 18-全市场同步"}
    latest = bars[-1]["trade_date"]
    feature, live = _feature_row(conn, cfg, code, bars)
    bands = _bands(conn, cfg, code, bars)
    candle = candles_mod.analyze(bars, len(bars) - 1)
    row = db.query_one(conn, "SELECT * FROM instruments WHERE code=?", (code,))
    instrument = dict(row) if row else {}          # sqlite3.Row 没有 .get()
    pooled = {item["code"] for item in watchlist_codes(cfg)}

    risk = None
    if feature:
        merged = {**feature, "close": bars[-1]["close"],
                  "quality_flag": feature.get("quality_flag") or "ok"}
        risk = risk_mod.assess(merged, bands, cfg, candle)

    contributions = [dict(row) for row in db.query(
        conn,
        """SELECT factor_id, raw_value, normalized_score, weight, contribution
           FROM factor_contributions WHERE code=? AND trade_date=?
           ORDER BY contribution DESC""",
        (code, latest))]
    alerts = [dict(row) for row in db.query(
        conn,
        """SELECT trade_date, level, signal_type, price, message, arbitrated_by, suppressed_by
           FROM alerts WHERE code=? ORDER BY trade_date DESC, id DESC LIMIT ?""",
        (code, DEFAULT_ALERTS))]
    hits = _screen_hits(conn, code)
    return {
        "ok": True, "code": code, "trade_date": latest, "bars": len(bars),
        "name": display_name(code, instrument.get("name") or "") or code,
        "instrument": instrument,
        "in_pool": code in pooled, "feature": feature, "live_feature": live,
        "bands": bands, "candle": candle, "risk": risk,
        "contributions": contributions, "alerts": alerts, "hits": hits,
        "close": bars[-1]["close"], "pct_chg": bars[-1].get("pct_chg"),
    }


def _screen_hits(conn, code: str, lookback: int = DEFAULT_SCREEN_LOOKBACK) -> dict:
    """最近这些筛选日里，它在哪些条件上出现过、当过几次主标签。"""
    rows = [dict(row) for row in db.query(
        conn,
        """SELECT trade_date, criterion, rank_no, close, outcome_5d, outcome_20d
           FROM screen_results WHERE code=?
           ORDER BY trade_date DESC LIMIT ?""",
        (code, lookback * 12))]
    if not rows:
        return {"days": 0, "criteria": {}, "latest": None, "outcomes": []}
    days = sorted({row["trade_date"] for row in rows}, reverse=True)[:lookback]
    rows = [row for row in rows if row["trade_date"] in days]
    criteria: dict[str, int] = {}
    for row in rows:
        criteria[row["criterion"]] = criteria.get(row["criterion"], 0) + 1
    outcomes = [(row["outcome_5d"], row["outcome_20d"]) for row in rows
                if row["outcome_20d"] is not None]
    return {
        "days": len(days), "criteria": criteria, "latest": rows[0], "rows": rows,
        "outcomes": outcomes,
        "avg20": (sum(item[1] for item in outcomes) / len(outcomes)) if outcomes else None,
    }


def _fmt(value, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}{suffix}"
    return f"{value}{suffix}"


def _pct(value, digits: int = 2) -> str:
    return "—" if value is None else f"{value:+.{digits}f}%"


def report(data: dict, with_buckets_text: str | None = None) -> str:
    if not data.get("ok"):
        return data.get("message", "查不到这只票")
    info = data["instrument"]
    feature = data.get("feature") or {}
    risk = data.get("risk") or {}
    candle = data.get("candle") or {}
    lines: list[str] = []
    lines.append(f"===== {data['code']} {data['name']} · 单票体检（数据截至 {data['trade_date']}）=====")
    lines.append(f"  类型 {info.get('type') or '—'}　板块 {info.get('board') or '—'}"
                 f"　{'在观察池里' if data['in_pool'] else '不在观察池'}"
                 f"　本地日线 {data['bars']} 根"
                 f"　上市日 {info.get('listed_date') or '—'}")
    if data.get("live_feature"):
        lines.append("  （池外的票没有日终特征，下面的状态与因子是**现算**的，口径与日终一致）")

    lines.append("")
    lines.append("① 行情")
    lines.append(f"  收盘 {_fmt(data['close'], 3)}　涨跌 {_pct(data.get('pct_chg'))}"
                 f"　量比 {_fmt(feature.get('vol_ratio_20'))}"
                 f"　60日均额 {_fmt((feature.get('avg_amount_60d') or 0) / 1e8, 2, '亿')}"
                 f"　换手 {_fmt(feature.get('turnover_rate'), 2, '%')}")

    lines.append("")
    lines.append("② 状态层（方向由它唯一给出）")
    lines.append(f"  状态 {feature.get('state') or '—'}（持续 {feature.get('state_days') or '—'} 天）"
                 f"　趋势分 {_fmt(feature.get('trend_score'), 1)}"
                 f"　机会分 {_fmt(feature.get('opportunity_score'), 3)}")
    if data["contributions"]:
        # 贡献是往趋势分上加的：正数把它推高（偏多），权重 0 的是观察期、不计分
        lines.append("  因子贡献（正 = 往趋势分上加，偏多；权重 0 的是观察期，不计分）：")
        for item in data["contributions"][:8]:
            mark = "" if item["weight"] else "　（观察期，不计分）"
            lines.append(f"    {item['factor_id']:<14} 原始 {_fmt(item['raw_value'], 3):>10}"
                         f"　归一 {_fmt(item['normalized_score'], 2):>6}"
                         f"　权重 {_fmt(item['weight'], 3):>6}"
                         f"　贡献 {_fmt(item['contribution'], 3):>7}{mark}")

    lines.append("")
    lines.append("③ 位置与结构")
    lines.append(f"  20 日区间位置 {_fmt(feature.get('range_position'), 3)}"
                 f"（0=贴低点，1=贴高点）　区间宽 {_fmt(feature.get('range_width_pct'), 1, '%')}")
    lines.append(f"  距 20 日高 {_pct(feature.get('raw_values', {}).get('dist_to_high20'))}"
                 f"　距 250 日高 {_pct(feature.get('dist_to_high_250'))}"
                 f"　斐波那契回撤 {_fmt(feature.get('retrace'), 3)}"
                 f"（0=贴高点，0.618=教科书买点）")
    swing = {1.0: "高低点同时抬升（HH+HL）", -1.0: "高低点同时下移", 0.0: "高低点不共振"}
    lines.append(f"  摆动结构 {swing.get(feature.get('swing_state'), '拐点不够')}"
                 f"　结构低点 {_fmt(feature.get('swing_low_1'), 3)}"
                 f"（{feature.get('bars_since_swing_low') or '—'} 根前确认，"
                 f"距现价 {_pct(feature.get('dist_to_swing_low'))}）"
                 f"　结构高点 {_fmt(feature.get('swing_high_1'), 3)}")

    lines.append("")
    lines.append("④ 形态与关键位")
    lines.append(f"  当日 K 线：{candle.get('pattern') or '无显著形态'}"
                 f"　反转确认：{'向上' if candle.get('reversal_up') else ('向下' if candle.get('reversal_down') else '无')}")
    if candle.get("neckline"):
        from_kind = "底" if candle.get("neckline_kind") == "support" else "顶"
        gap = (data["close"] - candle["neckline"]) / candle["neckline"] * 100
        state = "已跌破" if gap < 0 else "在上方"
        lines.append(f"  颈线 {_fmt(candle['neckline'], 3)}（来自{from_kind}部形态）"
                     f"　距现价 {_pct(gap)}，价格{state}")
    if data["bands"]:
        lines.append("  关键带：")
        for band in data["bands"]:
            center = (band["price_low"] + band["price_high"]) / 2
            gap = (data["close"] - center) / data["close"] * 100
            kind = {"support": "支撑", "resistance": "阻力", "range": "当前区间",
                    "neckline": "颈线"}.get(band["level_type"], band["level_type"])
            rng = (_fmt(band["price_low"], 3) if band["price_low"] == band["price_high"]
                   else f"{_fmt(band['price_low'], 3)} – {_fmt(band['price_high'], 3)}")
            lines.append(f"    {kind:<6}{rng:>22}　距现价 {_pct(gap):>8}"
                         f"　{band.get('engine') or ''}")

    lines.append("")
    lines.append("⑤ 风险层（做多大、错了怎么办）")
    if risk.get("stop_level") is None and not risk.get("position_cap"):
        lines.append(f"  0 仓：{risk.get('reason') or '—'}")
    else:
        lines.append(f"  仓位上限 {_fmt(risk.get('position_cap'), 3)}"
                     f"　止损 {_fmt(risk.get('stop_level'), 3)}（{_pct(risk.get('stop_pct'))}）"
                     f"　盈亏比 {_fmt(risk.get('risk_reward'))}"
                     f"　期望 {_fmt(risk.get('expectancy'), 2, 'R')}")
    for note in (risk.get("notes") or []):
        lines.append(f"    · {note}")

    lines.append("")
    lines.append(f"⑥ 提醒（最近 {len(data['alerts'])} 条）")
    if not data["alerts"]:
        lines.append("  没有提醒记录（默认沉默是设计，不是漏报）")
    for alert in data["alerts"]:
        why = ""
        if alert.get("suppressed_by"):
            why = f"（被压制：{alert['suppressed_by']}）"
        elif alert.get("arbitrated_by"):
            why = f"（裁决：{alert['arbitrated_by']}）"
        lines.append(f"  {alert['trade_date']} [{alert['level']}] {alert['signal_type']}"
                     f"　{alert['message']}{why}")

    hits = data.get("hits") or {}
    lines.append("")
    lines.append(f"⑦ 筛选命中（最近 {hits.get('days', 0)} 个筛选日）")
    if not hits.get("criteria"):
        lines.append("  这段窗口里一次都没被筛出来")
    else:
        total = sum(hits["criteria"].values())
        detail = "、".join(f"{key} {count} 次" for key, count in
                          sorted(hits["criteria"].items(), key=lambda kv: -kv[1]))
        lines.append(f"  共 {total} 次：{detail}")
        if hits.get("avg20") is not None:
            lines.append(f"  其中已回填的 {len(hits['outcomes'])} 条，平均 20 日 "
                         f"{_pct(hits['avg20'])}（这是它自己的历史，样本少时别当结论）")

    if with_buckets_text:
        lines.append("")
        lines.append("⑧ 它在全市场里处于什么位置")
        lines.append(with_buckets_text)
    return "\n".join(lines)


def bucket_position(conn, cfg: dict, code: str, days: int = 250) -> str:
    """这只票现在落在全市场分档的哪一格，那一格历史上 20 日表现如何。

    这一段是"结合系统"的关键：它把一只票的当前读数接到**同一套口径的历史统计**上，
    而不是凭"位置挺高"这种印象。用的是 26-分档研究 的同一套维度与分档。
    """
    from . import explore

    try:
        frame = explore.load_panel(conn, days=days, horizon=20)
    except explore.ExploreError as exc:
        return f"（跳过：{exc}）"
    mine = frame[frame["code"] == code]
    if mine.empty:
        return "（本地日线不够，算不出分档位置）"
    latest = mine.iloc[-1]

    lines = [f"  它在全市场分档里落在哪一格（对照最近 {days} 个交易日的历史统计）：",
             f"  {'维度':<8}{'当前值':>12}  {'落在哪一档':<16}{'该档20日超额':>12}"
             f"{'胜率':>7}{'样本':>11}"]
    for field, spec in explore.FIELDS.items():
        value = latest.get(spec["column"])
        if value is None or value != value:            # None 或 NaN
            continue
        table, _ = explore.bucket_table(frame, field)
        label = None
        for low, high, name in zip(spec["bins"], spec["bins"][1:], spec["labels"]):
            if low <= value < high:
                label = name
                break
        if label is None or label not in table.index:
            continue
        row = table.loc[label]
        if not row["样本"]:
            continue
        shown = f"{value / 1e8:.2f}亿" if field == "成交额" else f"{value:.2f}"
        lines.append(f"  {field:<8}{shown:>12}  {label:<16}{row['超额']:>11.2f}%"
                     f"{row['胜率']:>6.0f}%{int(row['样本']):>11,}")
    lines.append("  读法：超额 = 同一档历史平均 − 同一天全市场等权，正数说明这一格历史上跑赢；"
                 "格子里的样本越少越不可信。")
    return "\n".join(lines)
