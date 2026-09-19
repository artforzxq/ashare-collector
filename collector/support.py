"""疑似托底（"国家队"）：宽基 ETF 的份额净流入 + 二级市场异常放量。

两条口径就是建表时写下的那句话——**"份额抬升 + 成交放大 = 疑似托底"**：

  · **份额**来自一级市场申赎：只有真金白银申购才会让它增加 → 净流入的直接证据；
  · **成交额**是二级市场换手，里面混着做市、套利、情绪 → 单独放量说明不了什么。

两个条件同时成立才记一笔。只看成交额会把"换手热闹"误读成"有人进场"。

三个必须记住的限制：
  1. 份额是 **T+1 披露**的 → 这是**事后信号**，用来次日复盘和判断，做不了盘中实时；
  2. 单只 ETF 一天放量不稀奇 → 按**命中只数**分级：1~2 只 P2，≥ `multi_count` 只 P1（齐步走）；
  3. 净流入金额 = 份额变化 × 价格，净值优先、没有净值就用收盘价，明细里写明用的是哪个。

这一层**不改状态分**：它只往 alerts 里记一条（走现成的冷却与预算），
并在 support_days 留一行汇总，供页面、推送和事后回填。
"""

from __future__ import annotations

import json
import statistics

from . import db
from .names import display_name

# 默认盯这几只宽基：指数基金里"托底资金"最常出现的地方
DEFAULT_ETFS = ["SH510300", "SH510050", "SH510500", "SH512100", "SH588000", "SZ159919", "SZ159915"]

DEFAULTS = {
    "etfs": DEFAULT_ETFS,
    "shares_pct": 0.01,        # 份额增幅阈值：1%
    "amount_z": 2.0,           # 成交额 z 分数阈值：2 倍标准差
    "amount_ratio": 2.0,       # 备选口径：今天成交额 / 前 20 日均值 ≥ 2 倍（z 算不出时用它）
    "min_inflow": 200_000_000,  # 单只最低净流入 2 亿元，挡掉小 ETF 的噪声
    "multi_count": 3,          # 同时命中这么多只 → 升 P1（齐步走）
    "history_days": 60,        # 页面上看多少天
}


def settings(cfg: dict | None) -> dict:
    merged = dict(DEFAULTS)
    merged.update((cfg or {}).get("support") or {})
    return merged


def amount_stats(conn, code: str, trade_date: str, window: int = 20) -> dict:
    """当日成交额相对前 `window` 个交易日的"有多异常"。

    给两个口径：z 分数（标准做法）和倍数（今天 ÷ 之前均值）。
    倍数不是多余的——基准**完全平稳**（标准差 0）时 z 分数没有意义，那时候只有倍数能说话。
    """
    rows = [
        row["amount"]
        for row in db.query(
            conn,
            """SELECT amount FROM bars_daily WHERE code=? AND trade_date<=?
               AND amount IS NOT NULL ORDER BY trade_date DESC LIMIT ?""",
            (code, trade_date, window),
        )
    ]
    if len(rows) < 6:
        return {"today": rows[0] if rows else None, "mean": None, "z": None, "ratio": None}
    today, rest = rows[0], rows[1:]
    if len(rest) < 5:
        return {"today": today, "mean": None, "z": None, "ratio": None}
    mean = statistics.mean(rest)
    ratio = round(today / mean, 2) if mean > 0 else None
    sd = statistics.pstdev(rest)
    z = round((today - mean) / sd, 2) if sd > 0 else None
    return {"today": today, "mean": mean, "z": z, "ratio": ratio}


def scan(conn, cfg: dict | None = None, trade_date: str | None = None) -> dict:
    """算一遍"今天像不像有托底资金进场"。只读，不写库。"""
    conf = settings(cfg)
    if trade_date is None:
        row = db.query_one(conn, "SELECT MAX(trade_date) AS d FROM etf_shares")
        trade_date = row["d"] if row and row["d"] else None
    if not trade_date:
        return {"ok": False, "message": "还没有 ETF 份额数据，先跑一次 3-每日任务"}

    items: list[dict] = []
    thin = []                                   # 有份额、但只有一天，算不出变化的
    names = {
        row["code"]: row["name"]
        for row in db.query(conn, "SELECT code, name FROM instruments WHERE name IS NOT NULL")
    }
    for code in conf["etfs"]:
        rows = [
            dict(row)
            for row in db.query(
                conn,
                """SELECT trade_date, shares, nav FROM etf_shares
                   WHERE code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 2""",
                (code, trade_date),
            )
        ]
        if len(rows) < 2 or rows[0]["trade_date"] != trade_date:
            if rows:
                thin.append(code)         # 有数据，但只有一天——不是"没数据"，是"还差一天"
            continue
        today, prev = rows[0], rows[1]
        if not today["shares"] or not prev["shares"]:
            continue
        delta = today["shares"] - prev["shares"]
        pct = delta / prev["shares"]

        close = db.query_one(
            conn, "SELECT close FROM bars_daily WHERE code=? AND trade_date=?", (code, trade_date))
        close = close["close"] if close else None
        price = today.get("nav") or close
        price_basis = "净值" if today.get("nav") else ("收盘价" if close else None)
        inflow = delta * price if price else None

        stats = amount_stats(conn, code, trade_date)
        z, ratio, amount = stats["z"], stats["ratio"], stats["today"]
        # z 分数算得出来就用它；基准太平（标准差 0）时退回倍数判断
        spike = (z >= float(conf["amount_z"])) if z is not None else (
            ratio is not None and ratio >= float(conf["amount_ratio"]))
        hit = bool(
            pct >= float(conf["shares_pct"])
            and spike
            and inflow is not None and inflow >= float(conf["min_inflow"])
        )
        items.append({
            "code": code,
            "name": display_name(code, names.get(code)),
            "shares": today["shares"],
            "shares_prev": prev["shares"],
            "shares_delta": delta,
            "shares_pct": round(pct * 100, 2),
            "amount": amount,
            "amount_z": z,
            "amount_ratio": ratio,
            "spike_on": "z" if z is not None else "ratio",
            "price": price,
            "price_basis": price_basis,
            "inflow": inflow,
            "hit": hit,
        })

    hits = [item for item in items if item["hit"]]
    hits.sort(key=lambda item: -(item["inflow"] or 0))
    level = None
    if hits:
        level = "P1" if len(hits) >= int(conf["multi_count"]) else "P2"
    # 合计：光看单只 ETF 的份额变化，答不出"宽基整体是净申购还是净赎回"——
    # 而那才是"有没有人在用宽基进场"这句话的正题。
    # 口径说明：份额/净流入直接相加；增幅用 Σ份额 ÷ Σ前日份额 重算，
    # 不能用各只增幅的平均（小基金翻倍会把总数带跑偏）。
    total_shares = sum(item["shares"] for item in items if item.get("shares"))
    total_prev = sum(item["shares_prev"] for item in items if item.get("shares_prev"))
    priced = [item for item in items if item.get("inflow") is not None]
    totals = {
        "count": len(items),
        "shares": total_shares,
        "shares_prev": total_prev,
        "shares_delta": total_shares - total_prev,
        "shares_pct": round((total_shares / total_prev - 1) * 100, 2) if total_prev else None,
        "inflow": sum(item["inflow"] for item in priced) if priced else None,
        # 有哪几只算不出净流入（缺价格）要说清楚，不能把"算了一部分"当"合计"
        "unpriced": [item["code"] for item in items if item.get("inflow") is None],
    }
    return {
        "ok": True,
        "trade_date": trade_date,
        "thin": thin,
        "level": level,
        "totals": totals,
        "items": sorted(items, key=lambda item: -(item["inflow"] or 0)),
        "hits": hits,
        "net_inflow": sum(item["inflow"] or 0 for item in hits) if hits else 0.0,
        "thresholds": {
            "shares_pct": conf["shares_pct"],
            "amount_z": conf["amount_z"],
            "amount_ratio": conf["amount_ratio"],
            "min_inflow": conf["min_inflow"],
            "multi_count": conf["multi_count"],
        },
    }


def record(conn, cfg: dict | None, result: dict, verbose: bool = False) -> int:
    """把这次判定落库：support_days 一行汇总 + 命中的话往 alerts 记一条。

    只写"有命中"的日子，其余日子不留行——省得表里躺满"今天没事"。
    """
    if not result.get("ok") or not result.get("hits"):
        return 0
    hits = result["hits"]
    top = hits[0]
    detail = json.dumps([
        {key: item[key] for key in
         ("code", "shares_pct", "amount_z", "inflow", "price_basis")}
        for item in hits
    ], ensure_ascii=False)
    db.upsert_rows(conn, "support_days", [{
        "trade_date": result["trade_date"],
        "level": result["level"],
        "etf_count": len(hits),
        "net_inflow": round(result["net_inflow"], 2),
        "detail_json": detail,
        "created_at": db.now_iso(),
    }], ["trade_date"])

    # 提醒挂在"净流入最大的那只 ETF"上：alerts 的主键是 (code, 交易日, 信号类型, 级别)，
    # 冷却与每日预算是现成的，不用另造一套。
    amount_yi = result["net_inflow"] / 1e8
    db.upsert_rows(conn, "alerts", [{
        "created_at": db.now_iso(),
        "trade_date": result["trade_date"],
        "code": top["code"],
        "level": result["level"],
        "signal_type": "SUPPORT_INFLOW",
        "state": None,
        "price": top.get("price"),
        "message": (f"宽基 ETF 疑似托底：{len(hits)} 只同时份额净流入且放量，"
                    f"合计 {amount_yi:.1f} 亿元（{top['code']} 最大）"),
        "payload_json": detail,
        "feature_version": "support-v1",
        "arbitrated_by": "R6_PASS",
    }], ["code", "trade_date", "signal_type", "level"])
    if verbose:
        print(f"      疑似托底 {result['level']}：{len(hits)} 只，合计 {amount_yi:.1f} 亿元")
    return len(hits)


def history(conn, days: int = 60) -> list[dict]:
    """最近若干天的托底判定（页面/推送用）。"""
    return [
        dict(row)
        for row in db.query(
            conn,
            "SELECT * FROM support_days ORDER BY trade_date DESC LIMIT ?",
            (int(days),),
        )
    ]
