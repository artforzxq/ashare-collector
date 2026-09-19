"""观察池候选清单：从筛选结果里挑出"值得放进池子盯着"的票。

观察池该不该固定不变？答案是**分层**：
  · 指数和 ETF 是固定配置——它们是基准和观察工具，按定义不轮换；
  · 个股是机会仓——出现什么就盯什么，没有这回事就不该占位置。

这个模块只做"机器能做"的那一半：把筛出来的票按**可核实的证据**排好队，
交给人去点。它不替你决定买什么，也不自己改 config.yaml 的观察池——
动钱的决定留在人手里。

排序用的三类证据，都是能从本地数据里查证的：
  1. 反复出现：同一只票在多个筛选日 / 多个形态里被命中。一次命中是噪声，反复才是特征。
  2. 流动性：近 60 日日均成交额。买不进的票不是机会，门槛与筛选、回测共用（universe）。
  3. 历史兑现：筛出后 20 个交易日的超额收益（有回填才用）。形态到底有没有信息量靠这个看。

另外附一份"池内现状"：池子里的票各自什么状态、能建多大仓位、最近一次被筛出是什么时候。
要不要调出，依据的是同一批数字，不是感觉。
"""

from __future__ import annotations

import unicodedata

from . import (candles as candles_mod, db, features as features_mod, regime as regime_mod,
               risk as risk_mod, universe)
from .config import watchlist_codes
from .levels import build_levels
from .registry import FactorRegistry

DEFAULTS = {
    "days": 20,        # 回看多少个筛选日
    "top": 20,         # 打印多少条候选
    "min_hits": 2,     # 至少被命中几次才算候选（1 次是噪声）
    "exit_streak": 5,  # 连续这么多个筛选日没出现 → 提示"考虑移出"
    "exit_stale_days": 5,  # 筛选结果比最新交易日旧这么多交易日 → 整个出池提示作废
    "risk_top": 60,    # 只给排在前面的这些候选现算风险层（算一次要跑一遍特征+关键带）
}


def _params(cfg: dict | None) -> dict:
    merged = dict(DEFAULTS)
    merged.update((cfg or {}).get("candidates") or {})
    return merged


def _aggregate(conn, dates: list[str]) -> dict[str, dict]:
    """把筛选结果按代码聚起来：命中次数、涉及几个筛选日、哪几个形态、历史兑现。"""
    if not dates:
        return {}
    placeholders = ",".join("?" for _ in dates)
    rows = db.query(
        conn,
        f"""SELECT trade_date, criterion, rank_no, code, name, close, pct_chg,
                   state, trend_score, vol_ratio, outcome_20d
            FROM screen_results
            WHERE trade_date IN ({placeholders})
            ORDER BY trade_date DESC, rank_no""",
        tuple(dates),
    )
    grouped: dict[str, dict] = {}
    for row in rows:
        item = grouped.setdefault(row["code"], {
            "code": row["code"], "name": row["name"], "hits": 0,
            "dates": set(), "criteria": set(), "outcomes": [],
            "best_rank": None, "latest": None,
        })
        item["hits"] += 1
        item["dates"].add(row["trade_date"])
        item["criteria"].add(row["criterion"])
        if row["outcome_20d"] is not None:
            item["outcomes"].append(float(row["outcome_20d"]))
        if row["rank_no"] is not None and (item["best_rank"] is None or row["rank_no"] < item["best_rank"]):
            item["best_rank"] = row["rank_no"]
        if item["latest"] is None or row["trade_date"] > item["latest"]["trade_date"]:
            item["latest"] = dict(row)
        if row["name"] and not item["name"]:
            item["name"] = row["name"]
    return grouped


def _risk_snapshot(conn, cfg: dict, code: str, trade_date: str) -> dict:
    """候选现在能建多大仓位——用当前代码重算，不是上次日终留下的数字。"""
    feature, close, bars = _feature_row(conn, cfg, code, trade_date)
    if not feature or close is None:
        return {"cap": None, "stop_pct": None, "risk_reward": None,
                "risk_reason": "本地还没有这只票的日线", "state": None, "atr_pct": None}
    bands = [dict(r) for r in db.query(
        conn, "SELECT level_type, price_low, price_high, weight FROM levels WHERE code=? AND trade_date=?",
        (code, trade_date))]
    if not bands:
        # 池外的票没跑过日终任务，关键带得就地算——口径与日终一致（levels.build_levels）
        bands = build_levels(bars, close, cfg)
    candle = candles_mod.analyze(bars, len(bars) - 1) if len(bars) >= 3 else {}
    merged = {**feature, "close": close,
              "quality_flag": feature.get("quality_flag") or "ok"}
    result = risk_mod.assess(merged, bands, cfg, candle)
    return {
        "cap": result["position_cap"],
        "stop_pct": result["stop_pct"],
        "risk_reward": result["risk_reward"],
        "risk_reason": result["reason"],
        "state": merged.get("state"),
        "atr_pct": merged.get("atr_pct"),
    }


def _feature_row(conn, cfg: dict, code: str, trade_date: str) -> tuple[dict | None, float | None, list[dict]]:
    """特征从哪里来：先读日终算好的，池外的票没有就就地算一遍。

    只在观察池上跑日终任务，所以 features_daily 里只有池子里的票。候选本来就是池外的票，
    "查不到特征"是常态——不能因此把仓位一栏全空着，那这张清单就白排了。
    就地算的时候用同一套函数（features.compute_feature_series），口径不会两样。
    """
    bars = [dict(row) for row in db.query(
        conn,
        """SELECT * FROM bars_daily WHERE code=? AND trade_date<=?
           AND COALESCE(quality_flag,'ok')!='blocked'
           ORDER BY trade_date""",
        (code, trade_date),
    )]
    if not bars:
        return None, None, []
    close = bars[-1].get("close")
    stored = db.query_one(
        conn, "SELECT * FROM features_daily WHERE code=? AND trade_date=?", (code, trade_date))
    if stored:
        return dict(stored), close, bars
    if len(bars) < 30:
        return None, close, bars
    try:
        series = features_mod.compute_feature_series(bars, cfg, FactorRegistry(cfg.get("factors", []), "screen"), {})
    except Exception:
        return None, close, bars
    if not series:
        return None, close, bars
    return series[-1], series[-1].get("close") or close, bars


def _width(text: str) -> int:
    """显示宽度：中文按两格算。不然带中文的表格每行都会错位。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int, right: bool = False) -> str:
    space = " " * max(0, width - _width(text))
    return space + text if right else text + space


def _table(header: list[tuple[str, int, bool]], rows: list[list[tuple[str, bool]]]) -> list[str]:
    """按显示宽度拼一张表。header 给 (标题, 列宽, 是否右对齐)，rows 给 [(文本, 是否右对齐)]。

    表头的对齐方式跟着数据走——数字列右对齐、文本列左对齐，表头和内容才对得上。
    """
    lines = ["".join(_pad(title, width, right) for title, width, right in header).rstrip()]
    for row in rows:
        lines.append("".join(
            _pad(text, width, right) for (text, right), (_, width, _) in zip(row, header)).rstrip())
    return lines


def _sort_key(item: dict) -> tuple:
    """先看能不能建仓，再看反复出现的程度，最后看流动性。

    故意不做加权总分：总分会把"命中 3 次的小票"和"命中 1 次的大票"压成一个数，
    看的人无从判断哪条证据更硬。分列开，判断权交回给人。
    """
    return (
        0 if (item.get("cap") or 0) > 0 else 1,     # 能建仓的排前面
        0 if item["liquid"] else 1,                 # 买不进的排后面
        -item["days"],
        -len(item["criteria"]),
        -item["hits"],
        -(item["avg_amount_60d"] or 0.0),
        item["code"],
    )


def _cheap_sort_key(item: dict) -> tuple:
    """只按"不用现算"的证据排：流动性、反复程度、成交额。先拿它挑出要细看的那一批。"""
    return (
        0 if item["liquid"] else 1,
        -item["days"],
        -len(item["criteria"]),
        -item["hits"],
        -(item["avg_amount_60d"] or 0.0),
        item["code"],
    )


def _leading_misses(all_days: list[str], seen: set[str]) -> int:
    """从最新筛选日往回数，连续多少个筛选日没出现。

    all_days 已经是倒序；遇到第一次出现就停——"连续"是重点，不是"总共缺席了几次"。
    """
    streak = 0
    for day in all_days:
        if day in seen:
            break
        streak += 1
    return streak


def build(conn, cfg: dict, days: int | None = None, min_hits: int | None = None) -> dict:
    params = _params(cfg)
    days = int(params["days"] if days is None else days)
    min_hits = int(params["min_hits"] if min_hits is None else min_hits)
    exit_streak = int(params["exit_streak"])
    exit_stale_days = int(params["exit_stale_days"])
    risk_top = int(params["risk_top"])

    latest = db.query_one(conn, "SELECT MAX(trade_date) AS d FROM screen_results")
    latest_date = latest["d"] if latest else None
    if not latest_date:
        return {"ok": False, "message": "还没有筛选结果，先跑一次：python run.py screen"}

    all_days = [
        row["trade_date"]
        for row in db.query(conn, "SELECT DISTINCT trade_date FROM screen_results ORDER BY trade_date DESC")
    ]
    dates = all_days[:days]
    pooled = {item["code"]: item for item in watchlist_codes(cfg)}
    grouped = _aggregate(conn, dates)
    # 池内的票即使这个窗口里一次都没出现，也要有一行——
    # "最近没出现"正是出池要看的第一条证据，不能因为它没出现就从表里消失。
    for code in pooled:
        grouped.setdefault(code, {
            "code": code, "name": None, "hits": 0, "dates": set(), "criteria": set(),
            "outcomes": [], "best_rank": None, "latest": None,
        })
    threshold = universe.min_avg_amount(cfg)

    # 连续未命中用**全部**筛选日算，不用回看窗口：窗口只有 20 天的话，
    # 20 天前就没影的票会一律顶格，分不出"刚凉"和"凉很久"。
    appeared: dict[str, set[str]] = {code: set() for code in pooled}
    for row in db.query(conn, "SELECT DISTINCT code, trade_date FROM screen_results"):
        if row["code"] in appeared:
            appeared[row["code"]].add(row["trade_date"])

    # 筛选结果本身够不够新：拿"筛选日之后又过了几个交易日"算，周末和节假日不算数。
    # 尺子没量准的时候（比如两周没跑筛选），任何"该出池"的提示都是假警报。
    latest_trade = db.query_one(conn, "SELECT MAX(trade_date) AS d FROM bars_daily")
    latest_trade = latest_trade["d"] if latest_trade else None
    gap_days = 0
    if latest_trade and latest_trade > latest_date:
        row = db.query_one(
            conn,
            "SELECT COUNT(DISTINCT trade_date) AS n FROM bars_daily WHERE trade_date > ? AND trade_date <= ?",
            (latest_date, latest_trade),
        )
        gap_days = int(row["n"] or 0)
    screen_stale = gap_days > exit_stale_days

    items: list[dict] = []
    for item in grouped.values():
        if not item.get("name"):
            row = db.query_one(conn, "SELECT name FROM instruments WHERE code=?", (item["code"],))
            item["name"] = (row["name"] if row and row["name"] else item["code"])
        item["days"] = len(item["dates"])
        item["criteria"] = sorted(item["criteria"])
        item["avg_amount_60d"] = universe.avg_amount(conn, item["code"])
        item["excess_20d"] = (sum(item["outcomes"]) / len(item["outcomes"])) if item["outcomes"] else None
        item["outcome_n"] = len(item["outcomes"])
        item["liquid"] = (threshold <= 0 or item["avg_amount_60d"] is None
                          or item["avg_amount_60d"] >= threshold)
        item["threshold"] = threshold
        item["cap"] = None
        item["stop_pct"] = None
        item["risk_reward"] = None
        item["risk_reason"] = ""
        item["state"] = (item.get("latest") or {}).get("state")
        item["atr_pct"] = None
        item["risk_computed"] = False
        item["role"] = None
        item["type"] = None
        item["held"] = False
        items.append(item)

    # 风险层要现场算（池外的票没有日终结果），一只约 60ms；全市场跑满后候选能有几百只，
    # 全算一遍会让页面卡几十秒。所以先按便宜的证据排序，只给前 risk_top 只细算，
    # 其余留空并标明"未计算"。池内的票没几只，一律算全。
    cheap_sorted = sorted((item for item in items if item["code"] not in pooled), key=_cheap_sort_key)
    for item in cheap_sorted[:risk_top]:
        item.update(_risk_snapshot(conn, cfg, item["code"], latest_date))
        item["risk_computed"] = True
    for item in items:
        if item["code"] in pooled:
            item.update(_risk_snapshot(conn, cfg, item["code"], latest_date))
            item["risk_computed"] = True

    for item in items:
        # 出池证据：只在池内的票上算，且只提示不动作。
        unique = item["code"] in pooled
        item["miss_streak"] = _leading_misses(all_days, appeared.get(item["code"], set())) if unique else None
        item["amount_breach"] = bool(
            threshold > 0 and item["avg_amount_60d"] is not None and item["avg_amount_60d"] < threshold
        )
        item["cap_zero"] = item.get("cap") == 0
        reasons: list[str] = []
        if unique and item["miss_streak"] >= exit_streak:
            reasons.append(f"连续 {item['miss_streak']} 个筛选日没有出现")
        if unique and item["amount_breach"]:
            reasons.append("近 60 日均成交额已跌破门槛")
        # cap_zero 只显示、不作出池理由：风险层给 0 仓说的是"今天别加仓"，
        # 不是"这只票不该再跟踪"。实测里一只上升趋势的票当天也会是 0 仓。
        item["exit_reasons"] = reasons
        item["exit_hint"] = False          # 下面按角色与新鲜度逐个判定

    role_by_code = {code: meta["role"] for code, meta in pooled.items()}
    for item in items:
        if item["code"] not in role_by_code:
            continue
        item["role"] = role_by_code[item["code"]]
        item["type"] = pooled[item["code"]]["type"]
        item["held"] = item["role"] == "持仓"
        # 四条都满足才提示：有硬证据、是个股、不是持仓、筛选结果够新。
        item["exit_hint"] = bool(
            item["exit_reasons"]
            and item["type"] == "stock"        # 基准指数与 ETF 按定义固定配置，不参与轮换
            and not item["held"]               # 持仓的去留由仓位决定，不由形态决定
            and not screen_stale
        )

    candidates = [item for item in items if item["code"] not in pooled and item["hits"] >= min_hits]
    # 资产选择：市场层决定"眼下该用哪类工具表达"，只影响**排序**，不改筛选口径。
    # 依据（`run.py regime`，297 个交易日）：防守日筛出来的票 20 日超额 −3.04%，
    # 进取日 −0.90%，差 2.13pp（t=−2.41，校正后 p=0.047）——
    # 也就是说"防守时少出手、出手也优先用宽基"这句话在我们自己的信号上站得住。
    # 反过来不成立：三个档位都是显著为负，"进取时买个股更赚"没有证据，所以这里只排序、不推荐买入。
    mix = regime_mod.asset_mix(cfg, regime_mod.series(conn, cfg).get(latest_date))
    rank = {kind: -weight for kind, weight in mix["weights"].items()}
    # 候选是池外的票，`item["type"]` 一直是空的——类型只有池内那几行才带。
    # 不查这一下，几百只 ETF 会全被归到"其它"，资产选择就形同虚设。
    kinds = {row["code"]: row["type"] for row in db.query(conn, "SELECT code, type FROM instruments")}
    for item in candidates:
        item["kind"] = regime_mod.kind_of(cfg, item["code"], item.get("type") or kinds.get(item["code"]))
        item["kind_label"] = regime_mod.KIND_LABEL.get(item["kind"], item["kind"])
    candidates.sort(key=lambda item: (rank.get(item["kind"], 0), _sort_key(item)))

    members = [item for item in items if item["code"] in pooled]
    order = {code: index for index, code in enumerate(pooled)}
    members.sort(key=lambda item: order.get(item["code"], 999))

    return {
        "ok": True,
        "asset_mix": mix,
        "trade_date": latest_date,
        "window": dates,
        "days": days,
        "min_hits": min_hits,
        "threshold": threshold,
        "candidates": candidates,
        "members": members,
        "pooled_missing": [code for code in pooled if not grouped[code]["hits"]],
        # 出池口径与闸门也回传，页面照着显示，不自己编
        "exit_streak": exit_streak,
        "exit_stale_days": exit_stale_days,
        "screen_stale": screen_stale,
        "screen_gap_days": gap_days,
        "screen_days_total": len(all_days),
        "risk_top": risk_top,
    }


def report(result: dict, top: int | None = None, cfg: dict | None = None) -> str:
    if not result.get("ok"):
        return result.get("message", "候选清单算不出来")
    params = _params(cfg)
    top = int(params["top"] if top is None else top)
    lines: list[str] = []
    lines.append(f"===== 观察池候选清单 {result['trade_date']} =====")
    lines.append(f"回看 {len(result['window'])} 个筛选日"
                 f"（{result['window'][-1]} → {result['window'][0]}），至少命中 {result['min_hits']} 次；"
                 "指数和 ETF 是固定配置，不参与轮换。")
    lines.append("")

    candidates = result["candidates"][:top]
    if not candidates:
        lines.append("这次没有够格的候选（要么没重复命中，要么池子里已经有了）。")
    else:
        header = [("代码", 10, False), ("名称", 10, False), ("命中", 5, True), ("天数", 5, True),
                  ("形态", 18, False), ("收盘", 9, True), ("状态", 7, False), ("仓位", 6, True),
                  ("60日均额", 10, True), ("20日超额", 9, True)]
        rows: list[list[tuple[str, bool]]] = []
        for item in candidates:
            close = item["latest"]["close"]
            amount = item["avg_amount_60d"]
            rows.append([
                (item["code"], False),
                ((item["name"] or "")[:9], False),
                (str(item["hits"]), True),
                (str(item["days"]), True),
                (",".join(item["criteria"])[:17], False),
                (f"{close:.2f}" if close is not None else "—", True),
                (item.get("state") or "—", False),
                (f"{item['cap']:.2f}" if item.get("cap") is not None else "—", True),
                (f"{amount / 1e8:.2f}亿" if amount else "—", True),
                (f"{item['excess_20d']:+.1f}%" if item["excess_20d"] is not None else "—", True),
            ])
        lines.extend(_table(header, rows))
        lines.append("")
        zero = [item for item in candidates if not (item.get("cap") or 0) > 0]
        if zero:
            lines.append(f"其中 {len(zero)} 只当前算不出仓位——不是否定它，是现在不该动手：")
            for item in zero[:5]:
                lines.append(f"  {item['code']} {item['name'] or ''}：{item['risk_reason']}")
        thin = [item for item in candidates if not item["liquid"]]
        if thin:
            lines.append(f"另有 {len(thin)} 只近 60 日均额低于 {result['threshold'] / 1e4:,.0f} 万，"
                         "排在后面（买不进的票不算机会）。")
        lines.append("")
        lines.append("要加进观察池，直接复制这一行（会顺带把历史拉下来）：")
        lines.append(f"  python run.py add {' '.join(item['code'] for item in candidates[:10])}")

    lines.append("")
    lines.append("池内现状（按 config.yaml 的顺序；指数与 ETF 是固定配置，不参与轮换）")
    members = result["members"]
    if not members:
        lines.append("  池子是空的，或者这些票一次都没被筛出来过。")
    else:
        header = [("代码", 10, False), ("类型", 7, False), ("状态", 7, False),
                  ("仓位", 7, True), ("盈亏比", 8, True), ("最近命中", 13, True)]
        rows = [[
            (item["code"], False),
            (item["type"], False),
            (item.get("state") or "—", False),
            (f"{item['cap']:.2f}" if item.get("cap") is not None else "—", True),
            (f"{item['risk_reward']:.2f}" if item.get("risk_reward") is not None else "—", True),
            (item["latest"]["trade_date"] if item["latest"] else "从未", True),
        ] for item in members]
        lines.extend("  " + line for line in _table(header, rows))
        if result["pooled_missing"]:
            lines.append(f"  （{'、'.join(result['pooled_missing'])} 在回看窗口里没被筛出来过）")
    return "\n".join(lines)
