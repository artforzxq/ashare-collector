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

import time

from . import candles as candles_mod, db, features as features_mod, review, rules, universe
from .names import display_name
from .registry import FactorRegistry

DEFAULT_MIN_BARS = 80
DEFAULT_TOP = 30
# 筛出来的票后来怎么样，拿沪深300 同期涨跌做参照——没有参照的"平均涨了 3%"
# 说明不了任何事，可能只是那段时间大盘在涨。
BENCHMARK_CODE = "SH000300"

# config 里没配 criteria 时的兜底（与仓库里的 config.yaml 保持一致）
DEFAULT_CRITERIA = (
    {"key": "反转", "title": "下跌/震荡里出现放量长阳等反转确认", "sort": "body_pct",
     "when": [{"field": "state", "op": "!=", "value": "up"},
              {"field": "candle.reversal_up", "op": "==", "value": True},
              {"field": "close", "op": "<=", "compare": "ma20", "factor": 1.12}]},
    {"key": "低位横盘", "title": "跌下来之后横着缩量，等打底", "sort": "vol_shrink_ratio", "desc": False,
     "when": [{"field": "consolidation_days", "op": ">=", "value": 20},
              {"field": "vol_shrink_ratio", "op": "<=", "value": 0.7},
              {"field": "close", "op": "<=", "compare": "ma60", "factor": 1.0},
              {"field": "state", "op": "!=", "value": "up"}]},
    {"key": "蓄势", "title": "缩量横盘且贴着高位，等方向", "sort": "vol_shrink_ratio", "desc": False,
     "when": [{"field": "consolidation_days", "op": ">=", "value": 20},
              {"field": "vol_shrink_ratio", "op": "<=", "value": 0.6},
              {"field": "close", "op": ">", "compare": "ma60", "factor": 1.02},
              {"field": "state", "op": "!=", "value": "down"}]},
    {"key": "突破", "title": "放量突破区间上沿", "sort": "vol_ratio_20",
     "when": [{"field": "breakout_confirmed", "op": "==", "value": 1}]},
    {"key": "异动", "title": "成交额异常放大", "sort": "vol_ratio_20",
     "when": [{"field": "vol_ratio_20", "op": ">=", "value": 3}]},
    {"key": "趋势", "title": "上升趋势里趋势分最高", "sort": "trend_score",
     "when": [{"field": "state", "op": "==", "value": "up"},
              {"field": "trend_score", "op": "exists", "value": True}]},
    {"key": "回踩", "title": "上升趋势里回踩到关键带附近", "sort": "trend_score",
     "when": [{"field": "state", "op": "==", "value": "up"},
              {"field": "raw_values.dist_to_level", "op": "between", "value": [-1.5, 1.5]}]},
)


def criteria(cfg: dict) -> list[dict]:
    """从配置读出筛选条件（没配就用兜底），并补上默认值。"""
    raw = ((cfg or {}).get("screen") or {}).get("criteria") or DEFAULT_CRITERIA
    out: list[dict] = []
    for item in raw:
        key = (item.get("key") or "").strip()
        if not key:
            continue
        entry = {
            "key": key,
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
    return len(rows)


def _thresholds(cfg: dict) -> tuple[int, int]:
    screen = (cfg or {}).get("screen") or {}
    return int(screen.get("min_bars", DEFAULT_MIN_BARS)), int(screen.get("top", DEFAULT_TOP))


def _db_name(conn, code: str) -> str:
    row = db.query_one(conn, "SELECT name FROM instruments WHERE code=?", (code,))
    return (row["name"] if row else "") or ""


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
        last = series[-1]
        candle = candles_mod.analyze(bars, len(bars) - 1)
        rows.append(
            {
                "code": code,
                "name": display_name(code, _db_name(conn, code)) or code,
                "trade_date": last["trade_date"],
                "close": last.get("close"),
                "pct_chg": bars[-1].get("pct_chg"),
                "state": last.get("state"),
                "trend_score": last.get("trend_score"),
                "opportunity_score": last.get("opportunity_score"),
                "vol_ratio_20": last.get("vol_ratio_20"),
                "consolidation_days": last.get("consolidation_days"),
                "vol_shrink_ratio": last.get("vol_shrink_ratio"),
                "breakout_confirmed": last.get("breakout_confirmed"),
                "avg_amount_60d": last.get("avg_amount_60d"),
                "ma20": last.get("ma20"),
                "ma60": last.get("ma60"),
                "raw_values": last.get("raw_values") or {},
                "candle": candle,
                "pattern": candles_mod.describe(candle),
                "body_pct": candle.get("body_pct"),
                "reversal_up": candle.get("reversal_up"),
            }
        )
        if verbose and index % 500 == 0:
            speed = index / max(0.001, time.time() - started)
            left = (len(codes) - index) / max(0.001, speed)
            print(f"    已扫 {index}/{len(codes)}，预计还要 {left / 60:.1f} 分钟")

    grouped: dict[str, list[dict]] = {}
    for item in conditions:
        matched = [row for row in rows if rules.matches(row, item["when"])]
        matched.sort(key=lambda row: row.get(item["sort"]) if row.get(item["sort"]) is not None else -1,
                     reverse=item["desc"])
        grouped[item["key"]] = matched

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
            if row.get("pattern"):
                detail = row["pattern"]
            elif item["key"] == "蓄势":
                detail = f"蓄势 {row.get('consolidation_days')} 日，缩量至 {(row.get('vol_shrink_ratio') or 0):.2f}"
            elif item["key"] == "回踩":
                dist = (row.get("raw_values") or {}).get("dist_to_level")
                detail = f"距关键带 {dist:+.2f}%" if dist is not None else "贴近关键带"
            elif item["key"] == "趋势":
                detail = f"趋势分 {row.get('trend_score'):.1f}"
            else:
                detail = f"量比 {(row.get('vol_ratio_20') or 0):.2f}"
            payload.append({
                "trade_date": trade_date,
                "criterion": item["key"],
                "rank_no": rank,
                "code": row["code"],
                "name": row["name"],
                "close": row.get("close"),
                "pct_chg": row.get("pct_chg"),
                "state": row.get("state"),
                "trend_score": row.get("trend_score"),
                "vol_ratio": row.get("vol_ratio_20"),
                "detail": detail,
                "created_at": db.now_iso(),
            })
    if not payload:
        return 0
    conn.execute("DELETE FROM screen_results WHERE trade_date=?", (trade_date,))
    db.upsert_rows(conn, "screen_results", payload, ["trade_date", "criterion", "code"])
    return len(payload)


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
             "conditions": rules.describe_all(item["when"])} for item in items]
    if not trade_date:
        return {"trade_date": None, "groups": {}, "criteria": meta, "last_run": last_run}
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
    return {"trade_date": trade_date, "groups": groups, "criteria": meta, "last_run": last_run}


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
            lines.append(
                f"  {rank:>2}. {row['name']:<10} {row['code']:<9} 收 {row['close']:>8.2f} "
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


def performance_report(conn, min_sample: int = 20) -> str:
    """给人看的"筛出来的票后来怎么样"。没有样本就明说，不硬凑结论。"""
    stats = outcome_stats(conn)
    if not stats:
        return ""
    all_dates = [row["trade_date"] for row in db.query(conn, "SELECT DISTINCT trade_date FROM screen_results")]
    base20 = _benchmark_returns(conn, all_dates, 20)
    lines = ["", "筛选结果回填（筛出来的票后来怎么样了）"]
    lines.append(f"{'形态':<10}{'条数':>5}{'5日胜率':>9}{'5日均值':>9}{'20日胜率':>9}{'20日均值':>9}{'20日超额':>10}")
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
        lines.append(f"{row['criterion']:<10}{row['n']:>5}{win5:>9}{avg5:>9}{win20:>9}{avg20:>9}"
                     f"{excess:>10}")
    if base20:
        lines.append("")
        lines.append("超额 = 该形态 20 日均值 − 同期沪深300（按各自的筛选日期取基准）；"
                     "正数才说明形态本身有信息量。")
    waiting = [row for row in stats if not row["done20"]]
    if waiting:
        lines.append(f"（{len(waiting)} 个形态还一条结果都算不出来——筛选日之后的交易日不够。"
                     "这是正常的，等数据长出来会自动回填）")
    thin = [row for row in stats if row["done20"] and row["done20"] < min_sample]
    if thin:
        lines.append(f"（另有 {len(thin)} 个形态的 20 日样本不足 {min_sample} 条，只能当方向看）")
    return "\n".join(lines)
