"""全市场筛选：用同一套因子体系扫全市场，挑出值得进一步看的票。

**筛选条件不写死在代码里**，全部来自 config.yaml 的 screen.criteria（见 collector/rules.py
的规则格式说明）。加一个新形态 = 加一段配置，不用改代码；运行时还会同步进
screen_criteria 表，方便查询和复盘"当时用的是哪些条件"。

前提是本地已经有历史数据（先跑 18-全市场同步 补历史）。只要一只票的日线够 min_bars 根，
它就能参与筛选——补得越多，筛得越全。

刻意不做的事：不给结论、不给买卖建议、不排"最值得买"。它输出的是
"今天有这些票出现了值得看的形态"，判断仍然交给人。这也是整个系统的基调：
机械地描述事实，不做预测。
"""

from __future__ import annotations

import statistics
import time

from . import candles as candles_mod, db, features as features_mod, review, rules, stats as stats_mod, universe
from .names import display_name
from .registry import FactorRegistry

DEFAULT_MIN_BARS = 80
DEFAULT_TOP = 30
# 筛出来的票后来怎么样，拿沪深300 同期涨跌做参照——没有参照的"平均涨了 3%"
# 说明不了任何事，可能只是那段时间大盘在涨。
BENCHMARK_CODE = "SH000300"

# config 里没配 criteria 时的兜底（与仓库里的 config.yaml 保持一致）
DEFAULT_CRITERIA = (
    {"key": "反转", "priority": 20, "title": "下跌/震荡里出现放量长阳等反转确认", "sort": "body_pct",
     "when": [{"field": "state", "op": "!=", "value": "up"},
              {"field": "candle.reversal_up", "op": "==", "value": True},
              {"field": "close", "op": "<=", "compare": "ma20", "factor": 1.12}]},
    {"key": "低位横盘", "priority": 60, "title": "跌下来之后横着缩量，等打底",
     "sort": "vol_shrink_ratio", "desc": False,
     "when": [{"field": "consolidation_days", "op": ">=", "value": 20},
              {"field": "vol_shrink_ratio", "op": "<=", "value": 0.7},
              {"field": "close", "op": "<=", "compare": "ma60", "factor": 1.0},
              {"field": "state", "op": "!=", "value": "up"}]},
    {"key": "蓄势", "priority": 40, "title": "缩量横盘且贴着高位，等方向",
     "sort": "vol_shrink_ratio", "desc": False,
     "when": [{"field": "consolidation_days", "op": ">=", "value": 20},
              {"field": "vol_shrink_ratio", "op": "<=", "value": 0.6},
              {"field": "close", "op": ">", "compare": "ma60", "factor": 1.02},
              {"field": "state", "op": "!=", "value": "down"}]},
    {"key": "突破", "priority": 10, "title": "放量突破区间上沿", "sort": "vol_ratio_20",
     "when": [{"field": "breakout_confirmed", "op": "==", "value": 1}]},
    {"key": "放量阳线异动", "priority": 50, "title": "成交额放大到 3 倍以上，且收阳",
     "sort": "vol_ratio_20",
     "when": [{"field": "vol_ratio_20", "op": ">=", "value": 3},
              {"field": "candle.body_pct", "op": ">=", "value": 0}]},
    {"key": "放量阴线异动", "priority": 55, "title": "成交额放大到 3 倍以上，且收阴",
     "sort": "vol_ratio_20",
     "when": [{"field": "vol_ratio_20", "op": ">=", "value": 3},
              {"field": "candle.body_pct", "op": "<", "value": 0}]},
    {"key": "趋势", "priority": 90, "title": "上升趋势里趋势分最高", "sort": "trend_score",
     "when": [{"field": "state", "op": "==", "value": "up"},
              {"field": "trend_score", "op": "exists", "value": True}]},
    {"key": "回踩", "priority": 30, "title": "上升趋势里回踩到关键带附近", "sort": "trend_score",
     "when": [{"field": "state", "op": "==", "value": "up"},
              {"field": "raw_values.dist_to_level", "op": "between", "value": [-1.5, 1.5]}]},
)


def criteria(cfg: dict) -> list[dict]:
    """从配置读出筛选条件（没配就用兜底），并补上默认值。

    priority 只决定"一只票同时命中多个条件时，拿哪个当主标签"，数字越小越具体。
    没写就按配置里的出现顺序排（越靠前越优先）——这样老的 config 不改也能用。
    它不改变任何一条记录：一只票同时命中几个条件，几条记录就都在。
    """
    raw = ((cfg or {}).get("screen") or {}).get("criteria") or DEFAULT_CRITERIA
    out: list[dict] = []
    for index, item in enumerate(raw):
        key = (item.get("key") or "").strip()
        if not key:
            continue
        entry = {
            "key": key,
            "priority": _number_or(item.get("priority"), float(index + 1)),
            "title": item.get("title") or "",
            "why": item.get("why") or "",
            "sort": item.get("sort") or "trend_score",
            "desc": bool(item.get("desc", True)),
            "when": item.get("when") or [],
            "enabled": bool(item.get("enabled", True)),
        }
        if entry["enabled"]:
            out.append(entry)
    return out


def _number_or(value, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(fallback)


def primary_labels(criteria_items: list[dict], groups: dict) -> dict:
    """每只票的"主标签"：命中多个条件时取 priority 最小的那个。

    分组还在，一条记录都不删——多标签是证据（一只票既突破又放量，比只突破信息更多）。
    这里解决的只是"同一只票在五个分组里各刷一遍"的观感问题。
    """
    priority = {item["key"]: item["priority"] for item in criteria_items}
    best: dict[str, tuple] = {}
    for key, rows in groups.items():
        for rank, row in enumerate(rows):
            code = row.get("code")
            if not code:
                continue
            seat = (priority.get(key, 999.0), rank)
            if code not in best or seat < best[code][0]:
                best[code] = (seat, key)
    return {code: value[1] for code, value in best.items()}


def sync_criteria(conn, cfg: dict, effective_from: str) -> int:
    """把条件写进 screen_criteria 表：以后能查"当时用的是哪些规则"。"""
    items = criteria(cfg)
    if not items:
        return 0
    rows = []
    for item in items:
        rows.append({
            "key": item["key"],
            "title": item["title"],
            "why": item["why"],
            "sort_key": item["sort"],
            "sort_desc": 1 if item["desc"] else 0,
            "conditions": db.dump_json(item["when"]),
            "enabled": 1,
            "updated_at": db.now_iso(),
        })
    db.upsert_rows(conn, "screen_criteria", rows, ["key"])
    # 配置里已经删掉的条件标成停用，而不是留着 enabled=1——这张表是审计用的，
    # "当时用的规则"和"现在还在用的规则"必须分得清（异动拆成阴阳两条就是这种情况）。
    keys = [row["key"] for row in rows]
    placeholders = ",".join("?" for _ in keys)
    conn.execute(f"UPDATE screen_criteria SET enabled=0 WHERE key NOT IN ({placeholders})", tuple(keys))
    conn.commit()
    return len(rows)


def _thresholds(cfg: dict) -> tuple[int, int]:
    screen = (cfg or {}).get("screen") or {}
    return int(screen.get("min_bars", DEFAULT_MIN_BARS)), int(screen.get("top", DEFAULT_TOP))


def _db_name(conn, code: str) -> str:
    row = db.query_one(conn, "SELECT name FROM instruments WHERE code=?", (code,))
    return (row["name"] if row else "") or ""


def build_row(code: str, name: str, feature: dict, bar: dict, candle: dict) -> dict:
    """把"某一天的特征 + 那天的 K 线 + 那天的行情"拼成规则引擎要的行。

    扫描（只看最新一天）和历史重放（看每一天）共用这一个函数——两边拼出来的行
    必须是同一个形状，否则"重放出来的筛选"和"每天真跑一遍的筛选"会对不上。
    """
    return {
        "code": code,
        "name": name,
        "trade_date": feature["trade_date"],
        "close": feature.get("close"),
        "pct_chg": bar.get("pct_chg"),
        "state": feature.get("state"),
        "trend_score": feature.get("trend_score"),
        "opportunity_score": feature.get("opportunity_score"),
        "vol_ratio_20": feature.get("vol_ratio_20"),
        "consolidation_days": feature.get("consolidation_days"),
        "vol_shrink_ratio": feature.get("vol_shrink_ratio"),
        "breakout_confirmed": feature.get("breakout_confirmed"),
        "avg_amount_60d": feature.get("avg_amount_60d"),
        "ma20": feature.get("ma20"),
        "ma60": feature.get("ma60"),
        "raw_values": feature.get("raw_values") or {},
        "range_position": feature.get("range_position"),
        "range_width_pct": feature.get("range_width_pct"),
        "candle": candle,
        "pattern": candles_mod.describe(candle),
        "body_pct": candle.get("body_pct"),
        "reversal_up": candle.get("reversal_up"),
    }


def group_rows(rows: list[dict], conditions: list[dict]) -> dict[str, list[dict]]:
    """按条件分组，每组按它自己的 sort 字段排序。"""
    grouped: dict[str, list[dict]] = {}
    for item in conditions:
        matched = [row for row in rows if rules.matches(row, item["when"])]
        matched.sort(key=lambda row: row.get(item["sort"]) if row.get(item["sort"]) is not None else -1,
                     reverse=item["desc"])
        grouped[item["key"]] = matched
    return grouped


def scan(conn, cfg: dict, min_bars: int | None = None, limit: int | None = None,
         verbose: bool = True) -> dict:
    """把本地有足够历史的标的全部算一遍，按配置里的条件分组。"""
    default_min, _ = _thresholds(cfg)
    min_bars = min_bars or default_min
    conditions = criteria(cfg)
    if not conditions:
        return {"ok": False, "message": "config.yaml 的 screen.criteria 是空的，没有可用的筛选条件"}

    registry = FactorRegistry(cfg.get("factors", []), "screen")
    # 标的池统一走 universe：历史够长 + 近 60 日均成交额过门槛，和回测用同一套口径。
    # 小票不是"机会"，是成交不了、数据也经不起看的噪声，不该出现在筛选结果里。
    picked = universe.select_codes(conn, cfg, min_bars=min_bars, verbose=verbose)
    codes = picked["codes"]
    if limit:
        codes = codes[:limit]
    if not codes:
        return {"ok": False, "message": f"本地没有够 {min_bars} 根日线、且成交额过门槛的标的，先跑 18-全市场同步"}

    started = time.time()
    rows: list[dict] = []
    for index, code in enumerate(codes, 1):
        bars = [
            dict(row)
            for row in db.query(
                conn,
                """SELECT * FROM bars_daily WHERE code=? AND COALESCE(quality_flag,'ok')!='blocked'
                   ORDER BY trade_date""",
                (code,),
            )
        ]
        if len(bars) < min_bars:
            continue
        try:
            series = features_mod.compute_feature_series(bars, cfg, registry, {})
        except Exception:
            continue
        rows.append(build_row(code, display_name(code, _db_name(conn, code)) or code,
                              series[-1], bars[-1], candles_mod.analyze(bars, len(bars) - 1)))
        if verbose and index % 500 == 0:
            speed = index / max(0.001, time.time() - started)
            left = (len(codes) - index) / max(0.001, speed)
            print(f"    已扫 {index}/{len(codes)}，预计还要 {left / 60:.1f} 分钟")

    grouped = group_rows(rows, conditions)

    result = {
        "ok": True,
        "scanned": len(codes),
        "with_data": len(rows),
        "trade_date": max((row["trade_date"] for row in rows), default=None),
        "groups": grouped,
        "criteria": conditions,
        "seconds": round(time.time() - started, 1),
        "universe": picked,
    }
    if verbose:
        counts = "、".join(f"{item['key']} {len(grouped[item['key']])}" for item in conditions)
        print(f"  扫完 {len(rows)} 只（用时 {result['seconds']} 秒），命中：{counts}")
    return result


def describe_detail(key: str, row: dict) -> str:
    """一句话说明"为什么它在这一组里"。落库和报告共用，免得两处口径不一样。"""
    if row.get("pattern"):
        return row["pattern"]
    if key == "蓄势":
        return f"蓄势 {row.get('consolidation_days')} 日，缩量至 {(row.get('vol_shrink_ratio') or 0):.2f}"
    if key == "回踩":
        dist = (row.get("raw_values") or {}).get("dist_to_level")
        return f"距关键带 {dist:+.2f}%" if dist is not None else "贴近关键带"
    if key == "趋势":
        return f"趋势分 {row.get('trend_score'):.1f}"
    return f"量比 {(row.get('vol_ratio_20') or 0):.2f}"


def payload_row(trade_date: str, criterion: str, rank: int, row: dict) -> dict:
    """落库用的一行。日终扫描和历史重放都走这里，字段不会对不上。"""
    return {
        "trade_date": trade_date,
        "criterion": criterion,
        "rank_no": rank,
        "code": row["code"],
        "name": row.get("name"),
        "close": row.get("close"),
        "pct_chg": row.get("pct_chg"),
        "state": row.get("state"),
        "trend_score": row.get("trend_score"),
        "vol_ratio": row.get("vol_ratio_20"),
        "detail": describe_detail(criterion, row),
        "created_at": db.now_iso(),
    }


def save(conn, result: dict, top: int | None = None) -> int:
    """把筛选结果落库（页面读它）。每次覆盖同一天的结果，避免重复累积。"""
    if not result.get("ok") or not result.get("trade_date"):
        return 0
    trade_date = result["trade_date"]
    _, default_top = _thresholds({})
    top = top or int((result.get("criteria") or [{}])[0].get("top") or DEFAULT_TOP)
    hits = sum(len(result["groups"].get(item["key"], [])) for item in result["criteria"])
    db.log_health(conn, trade_date, "screen", "screen", "ok", result.get("scanned", 0), 0.0, 0,
                  f"命中 {hits} 条，扫描 {result.get('scanned', 0)} 只")

    payload: list[dict] = []
    for item in result["criteria"]:
        for rank, row in enumerate(result["groups"].get(item["key"], [])[:top], 1):
            payload.append(payload_row(trade_date, item["key"], rank, row))
    if not payload:
        return 0
    conn.execute("DELETE FROM screen_results WHERE trade_date=?", (trade_date,))
    db.upsert_rows(conn, "screen_results", payload, ["trade_date", "criterion", "code"])
    return len(payload)


def replay(conn, cfg: dict, days: int = 120, min_bars: int | None = None, top: int | None = None,
           verbose: bool = True) -> dict:
    """把筛选条件放到历史上的每一天重放一遍——今天就能拿到 5 / 20 日的真实表现。

    为什么需要它：筛选结果要等"筛出日之后 20 个交易日"才长得出 outcome，
    今天筛出来的票得等到下个月才知道后来怎么样。但历史数据已经在本地了，
    对过去每一天跑一遍同一套条件，等价于"那天真的跑了一次筛选"，
    唯一的区别是我们已经知道后面发生了什么。

    **没有未来函数**：每一天用的都是截至那一天收盘的数据（滚动窗口和状态机都是因果的），
    K 线形态也只看到那一天；收益口径与 16-信号复盘 完全一致（信号日收盘 → 次日收盘建仓 →
    持有 N 个交易日），所以两边算出来的数字可以直接比。

    代价：全市场一次要重算特征链，几百秒。特征只算一遍，之后每一天是顺带评估的，
    所以"重放 120 天"和"重放 20 天"的耗时差不多。
    """
    default_min, default_top = _thresholds(cfg)
    min_bars = min_bars or default_min
    top = top or default_top
    conditions = criteria(cfg)
    if not conditions:
        return {"ok": False, "message": "config.yaml 的 screen.criteria 是空的，没有可用的筛选条件"}
    if days < 21:
        return {"ok": False, "message": "至少要有 20 个交易日才回填得出 20 日表现，把 days 调到 21 以上"}

    registry = FactorRegistry(cfg.get("factors", []), "screen")
    picked = universe.select_codes(conn, cfg, min_bars=min_bars, verbose=verbose)
    codes = picked["codes"]
    if not codes:
        return {"ok": False, "message": f"本地没有够 {min_bars} 根日线、且成交额过门槛的标的，先跑 18-全市场同步"}

    started = time.time()
    # 命中先按 (日期, 条件) 攒着，每格只留排序需要的那几个字段——全市场几百天的命中量
    # 不值得把整行都留在内存里。
    buckets: dict[tuple, list] = {}
    scanned = 0
    for index, code in enumerate(codes, 1):
        bars = [
            dict(row)
            for row in db.query(
                conn,
                """SELECT * FROM bars_daily WHERE code=? AND COALESCE(quality_flag,'ok')!='blocked'
                   ORDER BY trade_date""",
                (code,),
            )
        ]
        if len(bars) < min_bars:
            continue
        try:
            series = features_mod.compute_feature_series(bars, cfg, registry, {})
        except Exception:
            continue
        scanned += 1
        name = display_name(code, _db_name(conn, code)) or code
        for position in range(max(0, len(bars) - days), len(bars)):
            candle = candles_mod.analyze(bars, position)
            row = build_row(code, name, series[position], bars[position], candle)
            trade_date = row.get("trade_date")
            if not trade_date:
                continue
            for item in conditions:
                if not rules.matches(row, item["when"]):
                    continue
                buckets.setdefault((trade_date, item["key"]), []).append(
                    (row.get(item["sort"]), row["code"], row.get("name"), row.get("close"),
                     row.get("pct_chg"), row.get("state"), row.get("trend_score"),
                     row.get("vol_ratio_20"), describe_detail(item["key"], row)))
        if verbose and index % 500 == 0:
            speed = index / max(0.001, time.time() - started)
            left = (len(codes) - index) / max(0.001, speed)
            print(f"    已重放 {index}/{len(codes)}，预计还要 {left / 60:.1f} 分钟")

    by_date: dict[str, list[dict]] = {}
    for (trade_date, key), hits in buckets.items():
        item = next(entry for entry in conditions if entry["key"] == key)
        # 排序口径与 group_rows 完全一致：取不到排序字段的当 -1，按方向排
        hits.sort(key=lambda hit: hit[0] if hit[0] is not None else -1, reverse=item["desc"])
        for rank, hit in enumerate(hits[:top], 1):
            by_date.setdefault(trade_date, []).append({
                "trade_date": trade_date, "criterion": key, "rank_no": rank,
                "code": hit[1], "name": hit[2], "close": hit[3], "pct_chg": hit[4],
                "state": hit[5], "trend_score": hit[6], "vol_ratio": hit[7],
                "detail": hit[8], "created_at": db.now_iso(),
            })

    written = 0
    for trade_date in sorted(by_date):
        payload = by_date[trade_date]
        conn.execute("DELETE FROM screen_results WHERE trade_date=?", (trade_date,))
        db.upsert_rows(conn, "screen_results", payload, ["trade_date", "criterion", "code"])
        written += len(payload)
    # 窗口之外的旧结果要清掉。这张表的含义是"按当前这套条件重放出来的历史"；
    # 混进上一次重放（可能是另一版形态口径）或更早的行，统计就会悄悄掺假——
    # 实测只差一天两行，但"看起来差不多"的错误最难发现，而这正是回放要防的事。
    stale = 0
    dates_all = sorted(by_date)
    if dates_all:
        stale = conn.execute(
            "DELETE FROM screen_results WHERE trade_date < ? OR trade_date > ?",
            (dates_all[0], dates_all[-1]),
        ).rowcount
    conn.commit()

    dates = sorted(by_date)
    if dates:
        db.log_health(conn, dates[-1], "screen", "screen", "ok", scanned, 0.0, 0,
                      f"历史重放 {len(dates)} 个交易日，命中 {written} 条")
        conn.commit()
    return {
        "ok": True,
        "scanned": scanned,
        "days": len(dates),
        "dates": dates,
        "first": dates[0] if dates else None,
        "last": dates[-1] if dates else None,
        "written": written,
        "stale_removed": stale,
        "seconds": round(time.time() - started, 1),
        "universe": picked,
    }


def load(conn, cfg: dict, trade_date: str | None = None, top: int | None = None) -> dict:
    """读最近一次的筛选结果（看盘页面用）。"""
    run = db.query_one(
        conn,
        "SELECT run_date, rows, error_msg FROM data_health WHERE task='screen' ORDER BY run_date DESC LIMIT 1",
    )
    last_run = {"trade_date": run["run_date"], "scanned": run["rows"], "note": run["error_msg"]} if run else None
    if trade_date is None:
        row = db.query_one(conn, "SELECT MAX(trade_date) AS d FROM screen_results")
        trade_date = row["d"] if row else None
    items = criteria(cfg)
    meta = [{"key": item["key"], "title": item["title"], "why": item["why"],
             "priority": item["priority"], "conditions": rules.describe_all(item["when"])}
            for item in items]
    if not trade_date:
        return {"trade_date": None, "groups": {}, "criteria": meta, "last_run": last_run, "primary": {}}
    _, default_top = _thresholds(cfg)
    top = top or default_top
    groups: dict[str, list[dict]] = {}
    for item in items:
        groups[item["key"]] = [
            dict(row)
            for row in db.query(
                conn,
                """SELECT code, name, close, pct_chg, state, trend_score, vol_ratio, detail
                   FROM screen_results WHERE trade_date=? AND criterion=?
                   ORDER BY rank_no LIMIT ?""",
                (trade_date, item["key"], top),
            )
        ]
    return {"trade_date": trade_date, "groups": groups, "criteria": meta,
            "last_run": last_run, "primary": primary_labels(items, groups)}


def report(result: dict, top: int = 15) -> str:
    if not result.get("ok"):
        return result.get("message", "筛选失败")
    lines = ["===== 全市场筛选 =====", ""]
    lines.append(f"扫描 {result['scanned']} 只，有效 {result['with_data']} 只，"
                 f"用时 {result['seconds']} 秒，数据截至 {result['trade_date']}")
    lines.append("说明：这里只列出现了什么形态，不是买入建议；形态之后怎么走，得看行情。")
    info = result.get("universe") or {}
    if info.get("dropped_liquidity"):
        lines.append(f"标的池：{info.get('total', 0)} 只可用，已剔除 {info['dropped_liquidity']} 只"
                     f"近 60 日均成交额低于 {(info.get('threshold') or 0) / 1e4:,.0f} 万的小票"
                     f"（门槛在 config.yaml 的 universe.min_avg_amount_60d）")
    primary = primary_labels(result["criteria"], result["groups"])
    label_count: dict[str, int] = {}
    for rows in result["groups"].values():
        for row in rows:
            label_count[row["code"]] = label_count.get(row["code"], 0) + 1
    shared = sum(1 for count in label_count.values() if count > 1)
    if shared:
        lines.append(f"命中 {len(primary)} 只票，其中 {shared} 只同时命中多个条件。"
                     f"下面每只票在它「主标签」那一组里标了 ★（优先级见 config.yaml 的 screen.criteria）——"
                     f"多标签是证据，不删；★ 只是告诉你该从哪一条开始看。")
    for item in result["criteria"]:
        rows = result["groups"].get(item["key"], [])
        lines.append("")
        lines.append(f"【{item['key']}】{item['title']}（{len(rows)} 只）")
        if item.get("why"):
            lines.append(f"  · {item['why']}")
        if item.get("when"):
            lines.append(f"  · 条件：{rules.describe_all(item['when'])}")
        if not rows:
            lines.append("  （今天没有）")
            continue
        for rank, row in enumerate(rows[:top], 1):
            trend = f"{row['trend_score']:.0f}" if row.get("trend_score") is not None else "—"
            vol = f"{row['vol_ratio_20']:.2f}" if row.get("vol_ratio_20") is not None else "—"
            amount = row.get("avg_amount_60d")
            amt = f"{amount / 1e4:,.0f}" if amount else "—"
            mark = "★" if primary.get(row["code"]) == item["key"] else " "
            lines.append(
                f" {mark}{rank:>3}. {row['name']:<10} {row['code']:<9} 收 {row['close']:>8.2f} "
                f"涨跌 {(row['pct_chg'] or 0):+6.2f}%  趋势 {trend:>4}  量比 {vol:>5}  均额万 {amt:>7}"
            )
    return "\n".join(lines)


def backfill_outcomes(conn, verbose: bool = False) -> dict:
    """回填筛选结果之后 5/20 个交易日的真实表现。

    口径与提醒复盘完全一致（信号日收盘确认、次日收盘建仓、持有 N 个交易日），
    这样"筛出来的票"和"提醒过的票"可以直接比。
    """
    cache: dict = {}
    updated = 0
    for row in db.query(
        conn,
        "SELECT trade_date, criterion, code, outcome_5d, outcome_20d FROM screen_results",
    ):
        dates, bars = review.load_series(conn, row["code"], cache)
        sets: list[str] = []
        params: list = []
        for horizon, column in zip(review.HORIZONS, ("outcome_5d", "outcome_20d")):
            value = review.forward_return(bars, dates, row["trade_date"], horizon)
            if value is not None and row[column] != value:
                sets.append(f"{column}=?")
                params.append(value)
        if sets:
            params.extend([row["trade_date"], row["criterion"], row["code"]])
            conn.execute(
                f"UPDATE screen_results SET {', '.join(sets)} "
                "WHERE trade_date=? AND criterion=? AND code=?",
                params,
            )
            updated += 1
    conn.commit()
    if verbose and updated:
        print(f"      筛选结果回填：{updated} 条")
    return {"screen": updated}


def outcome_stats(conn) -> list[dict]:
    """每种形态筛出来的票，后来 5/20 个交易日表现如何。"""
    return [
        dict(row)
        for row in db.query(
            conn,
            """
            SELECT criterion,
                   COUNT(*)                                                 AS n,
                   SUM(CASE WHEN outcome_5d IS NOT NULL THEN 1 ELSE 0 END)  AS done5,
                   SUM(CASE WHEN outcome_5d > 0 THEN 1 ELSE 0 END)          AS win5,
                   ROUND(AVG(outcome_5d), 2)                                AS avg5,
                   SUM(CASE WHEN outcome_20d IS NOT NULL THEN 1 ELSE 0 END) AS done20,
                   SUM(CASE WHEN outcome_20d > 0 THEN 1 ELSE 0 END)         AS win20,
                   ROUND(AVG(outcome_20d), 2)                               AS avg20
            FROM screen_results
            GROUP BY criterion
            ORDER BY n DESC
            """,
        )
    ]


def _benchmark_returns(conn, dates, horizon: int) -> dict:
    """基准（沪深300）在这些日期上持有 horizon 个交易日的收益。"""
    try:
        series, bars = review.load_series(conn, BENCHMARK_CODE, {})
    except Exception:
        return {}
    out = {}
    for day in dates:
        value = review.forward_return(bars, series, day, horizon)
        if value is not None:
            out[day] = value
    return out


def equal_weight_baseline(conn, cfg: dict, dates: list[str], horizon: int = 20,
                          min_bars: int | None = None) -> dict:
    """同一批筛选日上，"随便买一只同口径的票"平均能拿到多少（全市场等权基准）。

    为什么非要这一条：筛出来的票跑输沪深300，有两种可能——这批票整体就不如大盘，
    或者形态本身没有判断力。全市场等权基准把前一种可能排除掉：
    **比等权还低，才是形态自己的问题**。只看沪深300 会把市场结构误读成形态失效。
    """
    default_min, _ = _thresholds(cfg)
    min_bars = min_bars or default_min
    picked = universe.select_codes(conn, cfg, min_bars=min_bars, verbose=False)
    cache: dict = {}
    per_date: dict[str, list] = {day: [] for day in dates}
    for code in picked["codes"]:
        series, bars = review.load_series(conn, code, cache)
        for day in dates:
            value = review.forward_return(bars, series, day, horizon)
            if value is not None:
                per_date[day].append(value)
    means = [statistics.mean(per_date[day]) for day in dates if per_date[day]]
    medians = [statistics.median(per_date[day]) for day in dates if per_date[day]]
    if not means:
        return {"mean": None, "median": None, "days": 0, "universe": len(picked["codes"]),
                "per_date": {}}
    return {
        "mean": round(statistics.mean(means), 4),
        "median": round(statistics.mean(medians), 4),
        "days": len(means),
        "horizon": horizon,
        "universe": len(picked["codes"]),
        # 逐日基准：形态统计要按交易日聚合，所以"每天的基准"必须逐日落下来
        "per_date": {day: round(statistics.mean(values), 4)
                     for day, values in per_date.items() if values},
    }


def pattern_stats(conn, cfg: dict | None = None, horizon: int = 20,
                  baseline: dict | None = None) -> dict:
    """每种形态的超额表现：**按交易日聚合**的 t 值 + 按形态个数做多重检验校正。

    三件事一起做才算数：
      1. 同一天被筛出来的几十只票不是独立观测（一起涨一起跌），先按日取均值再统计；
      2. 同时看了 8 个形态，最好的那个的 t 值本身就被"挑"过一遍，要按形态数量惩罚；
      3. 超额要减基准——减沪深300 只是及格线，减"全市场等权"才是形态自己的信息量
         （全市场那套口径贵，只有 22-历史重放 会算；页面即时算时用沪深300）。

    baseline 给 {交易日: 基准收益%} 就用它（全市场等权口径），没给就现算沪深300。
    """
    column = f"outcome_{horizon}d"
    rows = [
        dict(row)
        for row in db.query(
            conn,
            f"""SELECT criterion, trade_date,
                       AVG({column})  AS avg,
                       COUNT({column}) AS done
                FROM screen_results
                GROUP BY criterion, trade_date
                ORDER BY trade_date""",
        )
    ]
    date_list = sorted({row["trade_date"] for row in rows})
    if baseline is None:
        baseline = _benchmark_returns(conn, date_list, horizon)
        baseline_kind = "hs300"
    else:
        baseline_kind = "market"
    if not baseline:
        baseline_kind = "missing"          # 基准序列取不到 —— 直说，别拿 0 当基准

    by_pattern: dict[str, dict[str, list[float]]] = {}
    samples: dict[str, int] = {}
    for row in rows:
        samples[row["criterion"]] = samples.get(row["criterion"], 0) + int(row["done"] or 0)
        base = baseline.get(row["trade_date"])
        if base is None or row["avg"] is None or not row["done"]:
            continue
        by_pattern.setdefault(row["criterion"], {}).setdefault(row["trade_date"], []).append(
            float(row["avg"]) - float(base)
        )

    tests = sum(1 for days in by_pattern.values() if days)
    items = []
    # 基准缺失、或者某个形态还没有回填结果时，也要把形态列出来（days=0），
    # 否则页面上会变成"什么都没有"，看不出是"没数据"还是"没算"。
    for criterion in sorted(samples):
        by_day = by_pattern.get(criterion, {})
        stats = stats_mod.daily_mean_stats(by_day)
        p_value = stats_mod.two_sided_p(stats["t"])
        items.append(
            {
                "criterion": criterion,
                "days": stats["days"],
                "samples": samples.get(criterion, 0),
                "excess": round(stats["mean"], 4) if stats["mean"] is not None else None,
                "excess_sd": round(stats["sd"], 4) if stats["sd"] is not None else None,
                "ci95": stats["ci95"],
                "t": stats["t"],
                "p": p_value,
                "p_adj": stats_mod.sidak_adjust(p_value, tests),
            }
        )
    items.sort(key=lambda item: (item["excess"] is None, -(item["excess"] or 0)))
    return {
        "patterns": items,
        "tests": tests,
        "crit_t": stats_mod.crit_t(tests),
        "baseline": baseline_kind,
        "horizon": horizon,
        "dates": len(date_list),
    }


def performance_report(conn, min_sample: int = 20, baseline: dict | None = None) -> str:
    """给人看的"筛出来的票后来怎么样"。没有样本就明说，不硬凑结论。

    给了 baseline（全市场等权，见 equal_weight_baseline）就多一列"相对全市场"——
    那一列才是形态自己的信息量：跑输沪深300 可能只是这批票整体不如大盘。
    """
    stats = outcome_stats(conn)
    if not stats:
        return ""
    all_dates = [row["trade_date"] for row in db.query(conn, "SELECT DISTINCT trade_date FROM screen_results")]
    base20 = _benchmark_returns(conn, all_dates, 20)
    lines = ["", "筛选结果回填（筛出来的票后来怎么样了）"]
    lines.append(f"{'形态':<10}{'条数':>5}{'5日胜率':>9}{'5日均值':>9}{'20日胜率':>9}"
                 f"{'20日均值':>9}{'vs沪深300':>10}"
                 + (f"{'vs全市场':>10}" if baseline and baseline.get("mean") is not None else ""))
    for row in stats:
        win5 = f"{row['win5'] / row['done5'] * 100:.0f}%" if row["done5"] else "—"
        win20 = f"{row['win20'] / row['done20'] * 100:.0f}%" if row["done20"] else "—"
        avg5 = f"{row['avg5']:+.2f}%" if row["avg5"] is not None else "—"
        avg20 = f"{row['avg20']:+.2f}%" if row["avg20"] is not None else "—"
        # 参照取"这个形态自己那几天的基准均值"，而不是全表最新一天——形态之间的日期不一样
        own_dates = [item["trade_date"] for item in db.query(
            conn, "SELECT DISTINCT trade_date FROM screen_results WHERE criterion=?", (row["criterion"],))]
        bench_values = [base20[day] for day in own_dates if day in base20]
        bench = sum(bench_values) / len(bench_values) if bench_values else None
        excess = f"{row['avg20'] - bench:+.2f}%" if (row["avg20"] is not None and bench is not None) else "—"
        vs_market = ""
        if baseline and baseline.get("mean") is not None and row["avg20"] is not None:
            vs_market = f"{row['avg20'] - baseline['mean']:+.2f}%"
        lines.append(f"{row['criterion']:<10}{row['n']:>5}{win5:>9}{avg5:>9}{win20:>9}{avg20:>9}"
                     f"{excess:>10}" + (f"{vs_market:>10}" if baseline and baseline.get("mean") is not None else ""))
    if base20:
        lines.append("")
        if baseline and baseline.get("mean") is not None:
            lines.append(f"vs沪深300 = 该形态 20 日均值 − 同期沪深300；vs全市场 = 同一批日期上"
                         f"「随便买一只同口径的票」的平均值（{baseline['mean']:+.2f}%，"
                         f"{baseline['universe']} 只等权，{baseline['days']} 个日期）。")
            lines.append("判断形态有没有信息量看**vs全市场**那一列：跑输沪深300 也可能只是这批票整体不如大盘。"
                         "正数才说明形态本身有价值。")
        else:
            lines.append("超额 = 该形态 20 日均值 − 同期沪深300（按各自的筛选日期取基准）；"
                         "正数才说明形态本身有信息量。")
    waiting = [row for row in stats if not row["done20"]]
    if waiting:
        lines.append(f"（{len(waiting)} 个形态还一条结果都算不出来——筛选日之后的交易日不够。"
                     "这是正常的，等数据长出来会自动回填）")
    thin = [row for row in stats if row["done20"] and row["done20"] < min_sample]
    if thin:
        lines.append(f"（另有 {len(thin)} 个形态的 20 日样本不足 {min_sample} 条，只能当方向看）")
    lines.extend(_pattern_stats_lines(conn, cfg=None, baseline=baseline))
    return "\n".join(lines)


def _pattern_stats_lines(conn, cfg: dict | None = None,
                         baseline: dict | None = None, horizon: int = 20) -> list[str]:
    """按交易日聚合 + 多重检验校正的那一段。报告里必须有，否则"最好的那个"没法看。"""
    per_date = (baseline or {}).get("per_date") or None
    result = pattern_stats(conn, cfg, horizon=horizon, baseline=per_date)
    if not result["patterns"]:
        return []
    if result["baseline"] == "missing":
        return ["", "按交易日聚合：算不出来——本地没有沪深300（SH000300）的日线，"
                    "跑一次 18-全市场同步 补上基准序列。"]
    kind = "全市场等权" if result["baseline"] == "market" else "沪深300"
    lines = [
        "",
        f"按交易日聚合（基准：{kind}；同时看了 {result['tests']} 个形态，"
        f"校正后 |t| ≥ {result['crit_t']:g} 才算显著）",
        f"{'形态':<10}{'交易日':>7}{'按日超额':>10}{'95%区间':>16}{'t':>8}{'校正p':>9}  结论",
    ]
    for item in result["patterns"]:
        if item["t"] is None:
            lines.append(f"{item['criterion']:<10}{item['days']:>7}"
                         f"{'—':>10}{'—':>16}{'—':>8}{'—':>9}  交易日不够")
            continue
        edge = f"±{item['ci95']:.2f}" if item["ci95"] is not None else "—"
        verdict = ("显著为负" if item["excess"] < 0 else "显著为正") if (
            item["p_adj"] is not None and item["p_adj"] < 0.05) else "不显著"
        lines.append(f"{item['criterion']:<10}{item['days']:>7}{item['excess']:>+9.2f}%"
                     f"{edge:>16}{item['t']:>8.2f}{item['p_adj']:>9.3f}  {verdict}")
    lines.append("说明：按日超额 = 该形态当日命中标的的平均收益 − 当日基准，先按日取均值再求 t；"
                 "同一天几十只票一起涨跌，按样本算误差棒会把噪声当结论。")
    return lines
