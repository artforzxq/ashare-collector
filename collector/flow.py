"""资金去向：今天钱在往哪走。

四把尺子，**同一个量纲（亿元）**——"钱多了还是少了"必须能横向比：
  ① 宽基 ETF 份额净流入      有人在一级市场申购/赎回，方向明确
  ② 行业/主题 ETF 份额净流入  与宽基反向时，就是"轮动"最直接的证据
  ③ 全市场成交额 + 放量倍数   是存量资金换仓，还是增量资金进场
  ④ 融资余额变化             有没有人借钱买股票（杠杆）

口径纪律：净流入 = 份额变化 × 价格（净值优先，退回收盘价）；
只有"前一天也有份额"的标的才参与；算不出的单独列出来，**不拿 0 冒充"没变化"**。
"""

from __future__ import annotations

import statistics

from . import db, regime

# 背离与分档回测的落点：沪深300。它是现成的、每天都有的全市场刻度。
BENCHMARK = "SH000300"


def _etf_flows(conn, cfg: dict, dates: list[str]) -> tuple[dict, list[str], int]:
    """把最近两个交易日的 ETF 份额变化换成净流入（亿元），按宽基 / 行业主题分开。

    为什么只算沪市："份额是 T+1 披露的"，而深交所那个接口只给"最新份额"，
    补不出历史——拿它算"较前一日"会得到一串 0.00%，那是假的。
    """
    if len(dates) < 2:
        return {}, [], 0, []
    today, prev = dates[0], dates[1]
    rows = [
        dict(row)
        for row in db.query(
            conn,
            """SELECT s.code, s.trade_date, s.shares, s.nav, s.source, b.close, i.type
                 FROM etf_shares s
                 LEFT JOIN bars_daily b ON b.code = s.code AND b.trade_date = s.trade_date
                 LEFT JOIN instruments i ON i.code = s.code
                WHERE s.trade_date IN (?, ?)""",
            (today, prev),
        )
    ]
    by_code: dict[str, dict] = {}
    for row in rows:
        by_code.setdefault(row["code"], {})[row["trade_date"]] = row

    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    by_exchange: dict[str, dict[str, int]] = {}
    unpriced: list[str] = []
    mixed: list[str] = []
    for code, pair in by_code.items():
        now, before = pair.get(today), pair.get(prev)
        if not now or not before or not before.get("shares") or not now.get("shares"):
            continue
        # **同一序列不能混源**：深交所按日披露、东财是快照，两家的份额本来就差几个百分点
        # （同一只深市 ETF：深交所 63.29 亿 vs 东财 63.41 亿）。拿甲家今天减乙家昨天，
        # 算出来的不是"申购赎回"，是两家口径的差——等于在一段序列里换了把尺子。
        if (now.get("source") or "") != (before.get("source") or ""):
            mixed.append(code)
            continue
        price = now.get("nav") or now.get("close")
        if not price:
            unpriced.append(code)
            continue
        delta = float(now["shares"]) - float(before["shares"])
        if abs(delta) < 1e-6:
            continue
        kind = regime.kind_of(cfg, code, now.get("type"))
        if kind not in ("broad_etf", "sector_etf"):
            continue
        totals[kind] = totals.get(kind, 0.0) + delta * float(price)
        counts[kind] = counts.get(kind, 0) + 1
        market = str(code)[:2].upper()
        by_exchange.setdefault(kind, {})
        by_exchange[kind][market] = by_exchange[kind].get(market, 0) + 1
    flows = {
        kind: {"inflow": round(value / 1e8, 2), "count": counts.get(kind, 0),
               "by_exchange": by_exchange.get(kind, {})}
        for kind, value in totals.items()
    }
    return flows, unpriced, len(by_code), mixed


def _amount_context(conn, trade_date: str, window: int = 60) -> dict:
    rows = [
        dict(row)
        for row in db.query(
            conn,
            """SELECT trade_date, total_amount, up_count, down_count, limit_up_count, median_pct_chg
                 FROM market_breadth WHERE trade_date<=? ORDER BY trade_date""",
            (trade_date,),
        )
    ]
    if not rows or not rows[-1].get("total_amount"):
        return {}
    today = rows[-1]
    history = [float(row["total_amount"]) for row in rows[:-1][-window:] if row.get("total_amount")]
    median = statistics.median(history) if history else None
    return {
        "amount": round(float(today["total_amount"]) / 1e12, 2),          # 万亿
        "amount_ratio": round(float(today["total_amount"]) / median, 2) if median else None,
        "up_count": today.get("up_count"),
        "down_count": today.get("down_count"),
        "limit_up": today.get("limit_up_count"),
        "median_pct": today.get("median_pct_chg"),
    }


def _margin_delta(conn, trade_date: str) -> dict:
    """融资余额与变化（亿元）——**按每个市场自己的披露日对齐**。

    交易所是 T+1 披露的，而且两市的节奏不一样：实测 09-18 那行，沪市的 data_date 是
    09-18、深市还是 09-17。如果直接拿"今天合计 − 昨天合计"，深市那一块会因为两天用
    的是同一个数而抵消掉，结果看起来是"两市变化"，实际只是**沪市的变化**。
    所以这里按市场分别取它自己最近两个披露日再相减，加起来才是两市的变化。
    """
    rows = [
        dict(row)
        for row in db.query(
            conn,
            """SELECT trade_date, market, data_date, financing_balance, securities_lending
                 FROM margin WHERE trade_date<=? ORDER BY trade_date DESC""",
            (trade_date,),
        )
    ]
    if not rows:
        return {}
    by_market: dict[str, list[dict]] = {}
    for row in rows:
        by_market.setdefault(row["market"], []).append(row)

    balance = 0.0
    delta = 0.0
    details: list[dict] = []
    has_delta = False
    for market, items in sorted(by_market.items()):
        # 同一个 data_date 可能被多个 trade_date 重复记录（补历史时就是这样），按披露日去重
        seen: dict[str, dict] = {}
        for item in items:
            day = item.get("data_date") or item["trade_date"]
            seen.setdefault(day, item)
        ordered = sorted(seen)
        latest = ordered[-1]
        current = seen[latest]
        current_total = float(current.get("financing_balance") or 0) + float(current.get("securities_lending") or 0)
        balance += current_total
        entry = {"market": market, "data_date": latest,
                 "balance": round(current_total / 1e12, 3)}
        if len(ordered) >= 2:
            previous = seen[ordered[-2]]
            previous_total = (float(previous.get("financing_balance") or 0)
                              + float(previous.get("securities_lending") or 0))
            entry["delta"] = round((current_total - previous_total) / 1e8, 2)
            entry["prev_date"] = ordered[-2]
            delta += current_total - previous_total
            has_delta = True
        details.append(entry)
    info = {
        "balance": round(balance / 1e12, 3),          # 万亿
        "data_date": max(entry["data_date"] for entry in details),
        "by_market": details,
    }
    if has_delta:
        info["delta"] = round(delta / 1e8, 2)
    return info


def verdict(flow: dict) -> str:
    """把四个读数翻译成一句话。**只描述事实与方向，不给买卖建议。**"""
    broad = (flow.get("broad_etf") or {}).get("inflow")
    sector = (flow.get("sector_etf") or {}).get("inflow")
    amount_ratio = (flow.get("amount") or {}).get("amount_ratio")
    margin = (flow.get("margin") or {}).get("delta")
    parts: list[str] = []
    if broad is not None and sector is not None:
        if broad < 0 <= sector:
            parts.append("宽基被赎回、行业/主题被申购——像是从宽基挪向行业（偏进攻的换仓）")
        elif sector < 0 <= broad:
            parts.append("行业/主题被赎回、宽基被申购——像是从行业收回宽基（偏防守的换仓）")
        elif broad > 0 and sector > 0:
            parts.append("宽基和行业都在被申购——有增量资金进场")
        else:
            parts.append("宽基和行业都在被赎回——整体在减仓")
    if amount_ratio is not None:
        parts.append(f"成交额是近 60 日中位数的 {amount_ratio:.2f} 倍"
                     + ("（放量，增量资金）" if amount_ratio >= 1.15 else
                        "（没放量，更像存量资金换仓）" if amount_ratio <= 0.95 else ""))
    if margin is not None:
        parts.append(f"融资余额{'+' if margin >= 0 else '−'}{abs(margin):.0f} 亿"
                     + ("（在加杠杆）" if margin > 0 else "（在降杠杆）"))
    return "；".join(parts) if parts else "数据不足，不做判断"


def snapshot(conn, cfg: dict, trade_date: str | None = None) -> dict:
    """给页面与 AI 用的资金去向读数。"""
    if trade_date is None:
        row = db.query_one(conn, "SELECT MAX(trade_date) AS d FROM etf_shares")
        trade_date = row["d"] if row and row["d"] else None
    if not trade_date:
        return {"ok": False, "message": "还没有 ETF 份额数据，先跑一次 3-每日任务"}
    dates = [
        row["trade_date"]
        for row in db.query(
            conn,
            "SELECT DISTINCT trade_date FROM etf_shares WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 2",
            (trade_date,),
        )
    ]
    flows, unpriced, covered, mixed = _etf_flows(conn, cfg, dates)
    # 口径标注：这份统计里**哪些市场进来了、哪些没进来**，就写在数字旁边——
    # "沪市 911 只"和"深市暂缺（交易所只给最新份额）"是一眼要看出来的前提，
    # 藏在说明小字里等于没说。
    scope = {
        "dates": dates,
        "codes": covered,
        "by_exchange": (flows.get("broad_etf", {}).get("by_exchange") or {}),
        "missing_exchange": [] if any(
            (flows.get(kind, {}).get("by_exchange") or {}).get("SZ")
            for kind in ("broad_etf", "sector_etf")) else ["SZ"],
    }
    payload = {
        "ok": True,
        "trade_date": trade_date,
        "prev_date": dates[1] if len(dates) > 1 else None,
        "broad_etf": flows.get("broad_etf", {"inflow": None, "count": 0}),
        "sector_etf": flows.get("sector_etf", {"inflow": None, "count": 0}),
        "amount": _amount_context(conn, trade_date),
        "margin": _margin_delta(conn, trade_date),
        "unpriced": unpriced[:10],
        "mixed_source": mixed[:10],
        "covered": covered,
        "scope": scope,
        "regime": regime.latest(conn, cfg, trade_date),
    }
    payload["verdict"] = verdict(payload)
    payload["detail"] = etf_detail(conn, cfg, trade_date)
    payload["divergence"] = divergence(conn, cfg)
    payload["concentration"] = concentration(conn, cfg, trade_date)
    return payload


def _code_flows(conn, cfg: dict, trade_date: str | None = None) -> tuple[list[dict], str | None, str | None]:
    """每只 ETF 当天的净流入明细（同一套口径：同源比较、缺价格不计入）。

    `etf_detail` 与"按指数聚合的背离"都从这里取，免得两处各写一遍过滤条件。
    """
    if trade_date is None:
        row = db.query_one(conn, "SELECT MAX(trade_date) AS d FROM etf_shares")
        trade_date = row["d"] if row and row["d"] else None
    if not trade_date:
        return [], None, None
    prev_row = db.query_one(
        conn,
        """SELECT trade_date FROM etf_shares WHERE trade_date<?
            ORDER BY trade_date DESC LIMIT 1""",
        (trade_date,),
    )
    if not prev_row:
        return [], trade_date, None
    prev = prev_row["trade_date"]
    rows = [
        dict(row)
        for row in db.query(
            conn,
            """SELECT s.code, s.trade_date, s.shares, s.nav, s.source, b.close, b.pct_chg,
                      i.name, i.type
                 FROM etf_shares s
                 LEFT JOIN bars_daily b ON b.code = s.code AND b.trade_date = s.trade_date
                 LEFT JOIN instruments i ON i.code = s.code
                WHERE s.trade_date IN (?, ?)""",
            (trade_date, prev),
        )
    ]
    by_code: dict[str, dict] = {}
    for row in rows:
        by_code.setdefault(row["code"], {})[row["trade_date"]] = row
    items: list[dict] = []
    for code, pair in by_code.items():
        now, before = pair.get(trade_date), pair.get(prev)
        if not now or not before or not before.get("shares") or not now.get("shares"):
            continue
        if (now.get("source") or "") != (before.get("source") or ""):
            continue                      # 混源不比（理由见 _etf_flows）
        price = now.get("nav") or now.get("close")
        if not price:
            continue
        delta = float(now["shares"]) - float(before["shares"])
        if abs(delta) < 1e-6:
            continue
        name = now.get("name") or code
        items.append({
            "code": code,
            "name": name,
            "kind": regime.KIND_LABEL.get(regime.kind_of(cfg, code, now.get("type")), ""),
            "benchmark": benchmark_for(cfg, name),
            "inflow": round(delta * float(price) / 1e8, 2),
            "shares_pct": round(delta / float(before["shares"]) * 100, 2),
            "pct_chg": now.get("pct_chg"),
            "price": float(price),
        })
    items.sort(key=lambda item: -item["inflow"])
    return items, trade_date, prev


def _index_change(conn, code: str, days: int) -> dict | None:
    """指数当日涨跌与近 N 个交易日累计涨跌幅。"""
    rows = [
        dict(row)
        for row in db.query(
            conn,
            """SELECT trade_date, COALESCE(close_adj, close) AS close, pct_chg
                 FROM bars_daily WHERE code=? ORDER BY trade_date DESC LIMIT ?""",
            (code, days + 1),
        )
    ]
    if len(rows) < 2:
        return None
    rows.reverse()
    return {
        "code": code,
        "daily_pct": rows[-1].get("pct_chg"),
        "window_pct": round((float(rows[-1]["close"]) / float(rows[0]["close"]) - 1) * 100, 2),
        "window_start": rows[0]["trade_date"],
    }


def etf_detail(conn, cfg: dict, trade_date: str | None = None, limit: int = 6) -> dict:
    """明细：今天被申购最多 / 被赎回最多的 ETF 各几只。

    汇总只能看出"宽基在流出、行业在流入"，看不出**具体流到哪个方向**——
    而后者才是"钱去了哪"的正题。明细是同一套数字往下钻一层，不加新口径。
    """
    items, trade_date, prev = _code_flows(conn, cfg, trade_date)
    if not items:
        return {"inflow_top": [], "outflow_top": [], "trade_date": trade_date, "prev_date": prev}
    inflow_items = [item for item in items if item["inflow"] > 0]
    outflow_items = [item for item in items if item["inflow"] < 0]
    return {
        "trade_date": trade_date,
        "prev_date": prev,
        "inflow_top": inflow_items[:limit],
        "outflow_top": outflow_items[-limit:][::-1],
    }


# 宽基 ETF 名字 → 它跟踪的指数。用来算"一级市场申赎 vs 二级市场涨跌"的背离。
# 为什么按名字：ETF 与指数的对应关系是发行时就定死的，我们没有那张表，
# 但宽基 ETF 的简称里一定带着指数名（"华泰柏瑞沪深300ETF"）。行业 ETF 不参与——
# 一个行业对应几十只指数，硬配会配错。
DEFAULT_BENCHMARKS = (
    ("上证50", "SH000016"), ("沪深300", "SH000300"), ("中证500", "SH000905"),
    ("中证1000", "SH000852"), ("科创50", "SH000688"), ("科创100", "SH000698"),
    ("创业板", "SZ399006"), ("深证100", "SZ399330"), ("中证A500", "SH000510"),
    ("上证红利", "SH000015"),
)


def benchmarks(cfg: dict | None = None) -> list[tuple[str, str]]:
    """ETF 简称关键词 → 指数代码。配置里能加，加不了就落回内置那几条。

    匹配有顺序：**长关键词优先**（"中证A500"要在"中证500"之前判，否则前者会被后者吃掉）。
    """
    configured = ((cfg or {}).get("market") or {}).get("benchmarks") or {}
    table = [(str(key), str(value)) for key, value in configured.items()]
    merged = table or list(DEFAULT_BENCHMARKS)
    return sorted(merged, key=lambda pair: -len(pair[0]))


def benchmark_for(cfg: dict | None, name: str) -> str | None:
    text = str(name or "")
    for hint, code in benchmarks(cfg):
        if hint in text:
            return code
    return None


def divergence(conn, cfg: dict, days: int = 20) -> dict:
    """一级市场（ETF 申赎）与二级市场（指数涨跌）的**背离**。

    口径写死成两句话，免得这个数越读越玄：
      · 当日：宽基 ETF 合计净流入的**符号** vs 沪深300 当日涨跌的**符号**；
      · 窗口：近 N 个交易日累计净流入 vs 沪深300 同期累计涨跌幅。
    方向相反 = 背离。**指数涨 + 宽基净赎回**，说明这波不是靠 ETF 申购推上去的
    （钱更可能来自个股与杠杆）；**指数跌 + 宽基净申购**，说明有人在用宽基接。
    """
    bench = BENCHMARK
    rows = [
        dict(row)
        for row in db.query(
            conn,
            """SELECT trade_date, COALESCE(close_adj, close) AS close
                 FROM bars_daily WHERE code=? ORDER BY trade_date DESC LIMIT ?""",
            (bench, days + 1),
        )
    ]
    if len(rows) < 2:
        return {"ok": False, "message": "没有基准指数日线，算不出背离"}
    rows.reverse()
    window_start = rows[0]["trade_date"]
    bench_change = (float(rows[-1]["close"]) / float(rows[0]["close"]) - 1) * 100

    daily = history(conn, cfg, days)
    window_flow = sum(item.get("broad_etf") or 0 for item in daily if item["trade_date"] > window_start)
    today_flow = (daily[-1].get("broad_etf") if daily else None)
    today_change = None
    today_row = db.query_one(
        conn, "SELECT pct_chg FROM bars_daily WHERE code=? AND trade_date=?", (bench, daily[-1]["trade_date"])
    ) if daily else None
    if today_row:
        today_change = today_row["pct_chg"]

    def opposed(flow_value, price_value) -> bool | None:
        if flow_value is None or price_value is None:
            return None
        if abs(flow_value) < 1e-9 or abs(price_value) < 1e-9:
            return None
        return (flow_value > 0) != (price_value > 0)

    # 按指数聚合：每个宽基指数看"跟踪它的那些 ETF 合计净流入"和它自己的涨跌。
    # 只看沪深300 会把"科创50ETF 在赎回、而科创50 指数在涨"这类结构差异盖掉——
    # 那也是背离，只不过是某个板块内部的。
    items, _, _ = _code_flows(conn, cfg)
    grouped: dict[str, dict] = {}
    for item in items:
        code = item.get("benchmark")
        if not code:
            continue
        slot = grouped.setdefault(code, {"index": code, "etf_flow": 0.0, "etf_count": 0})
        slot["etf_flow"] += item["inflow"]
        slot["etf_count"] += 1
    by_benchmark = []
    for code, slot in grouped.items():
        change = _index_change(conn, code, days)
        if not change:
            continue
        entry = {
            "index": code,
            "name": (db.query_one(conn, "SELECT name FROM instruments WHERE code=?", (code,)) or {"name": code})["name"],
            "etf_flow": round(slot["etf_flow"], 2),
            "etf_count": slot["etf_count"],
            "index_pct": change["daily_pct"],
            "window_index_pct": change["window_pct"],
        }
        entry["diverged"] = opposed(entry["etf_flow"], entry["index_pct"])
        by_benchmark.append(entry)
    # 背离的排前面：那才是要看的东西
    by_benchmark.sort(key=lambda item: (item["diverged"] is not True, item["etf_flow"]))

    daily_diverged = opposed(today_flow, today_change)
    window_diverged = opposed(window_flow, bench_change)
    notes = []
    if daily_diverged is True:
        notes.append("指数与宽基资金反向：上涨不是 ETF 申购推的" if (today_change or 0) > 0
                     else "指数下跌而宽基被申购：有人在用宽基接")
    elif daily_diverged is False:
        notes.append("指数与宽基资金同向：ETF 申购与上涨/下跌方向一致")
    if window_diverged is True:
        notes.append(f"近 {days} 个交易日累计也背离（宽基 {window_flow:+.1f} 亿 vs 沪深300 {bench_change:+.2f}%）")
    return {
        "ok": True,
        "days": days,
        "benchmark": bench,
        "window_start": window_start,
        "daily": {"flow": today_flow, "index_pct": today_change, "diverged": daily_diverged},
        "window": {"flow": round(window_flow, 2), "index_pct": round(bench_change, 2),
                   "diverged": window_diverged},
        "by_benchmark": by_benchmark,
        "note": "；".join(notes) or "样本不足",
    }


def history(conn, cfg: dict, days: int = 20) -> list[dict]:
    """最近 N 个交易日的资金去向（给页面画个简单趋势）。"""
    dates = [
        row["trade_date"]
        for row in db.query(
            conn,
            "SELECT DISTINCT trade_date FROM etf_shares ORDER BY trade_date DESC LIMIT ?",
            (days + 1,),
        )
    ]
    out = []
    for index in range(len(dates) - 1):
        pair = dates[index:index + 2]
        flows, _, _, _ = _etf_flows(conn, cfg, pair)
        out.append({
            "trade_date": pair[0],
            "broad_etf": flows.get("broad_etf", {}).get("inflow"),
            "sector_etf": flows.get("sector_etf", {}).get("inflow"),
        })
    return list(reversed(out))


def _concentration_of_day(conn, trade_date: str, big_amount: float) -> dict | None:
    """某一天个股成交额的集中度：前 100 占比 / 前 10% 占比 / HHI / 大票只数。"""
    amounts = sorted(
        (
            float(row["amount"])
            for row in db.query(
                conn,
                """SELECT b.amount FROM bars_daily b JOIN instruments i ON i.code = b.code
                    WHERE b.trade_date=? AND i.type='stock' AND b.amount>0""",
                (trade_date,),
            )
        ),
        reverse=True,
    )
    if len(amounts) < 1000:          # 样本太少时"集中度"没有意义
        return None
    total = sum(amounts)
    if total <= 0:
        return None
    cut = max(1, int(len(amounts) * 0.1))
    return {
        "trade_date": trade_date,
        "total": total,
        "count": len(amounts),
        "top100_pct": round(sum(amounts[:100]) / total * 100, 2),
        "top10pct_pct": round(sum(amounts[:cut]) / total * 100, 2),
        # HHI：Σ(份额²) × 10000。标准集中度度量，对头部特别敏感、不受"只数"影响。
        "hhi": round(sum((value / total) ** 2 for value in amounts) * 10000, 2),
        "big_count": sum(1 for value in amounts if value >= big_amount),
    }


def concentration(conn, cfg: dict, trade_date: str | None = None) -> dict:
    """个股成交额集中度：**缩量大涨时用来区分"存量抱团"还是"增量进场"**。

    只看成交额总量区分不了这两件事：
      · 增量进场 = 总量放大 + 集中度不升（钱铺开了）；
      · 存量抱团 = 总量不放大 + 集中度升高（钱挤向少数方向）。
    所以这个数**必须和历史分位一起读**：不同市值结构下绝对值没法横比，分位可以。
    """
    conf = ((cfg or {}).get("market") or {}).get("concentration") or {}
    window = int(conf.get("window", 60))
    top = int(conf.get("top", 10))
    big_amount = float(conf.get("big_amount", 5_000_000_000))
    if trade_date is None:
        row = db.query_one(conn, "SELECT MAX(trade_date) AS d FROM bars_daily")
        trade_date = row["d"] if row and row["d"] else None
    if not trade_date:
        return {"ok": False, "message": "本地还没有日线数据"}

    dates = [
        row["trade_date"]
        for row in db.query(
            conn,
            """SELECT DISTINCT b.trade_date FROM bars_daily b JOIN instruments i ON i.code = b.code
                WHERE i.type='stock' AND b.trade_date<=? ORDER BY b.trade_date DESC LIMIT ?""",
            (trade_date, window + 1),
        )
    ]
    series = []
    for day in sorted(dates):
        entry = _concentration_of_day(conn, day, big_amount)
        if entry:
            series.append(entry)
    if not series:
        return {"ok": False, "message": "算不出集中度（个股成交额样本不够）"}
    today = series[-1]
    history = series[:-1]

    def percentile(key: str) -> float | None:
        values = [entry[key] for entry in history]
        if not values:
            return None
        return round(sum(1 for value in values if value < today[key]) / len(values) * 100, 1)

    top10 = [
        {"code": row["code"], "name": row["name"] or row["code"],
         "amount": round(float(row["amount"]) / 1e8, 2), "pct_chg": row["pct_chg"]}
        for row in db.query(
            conn,
            """SELECT b.code, COALESCE(i.name, b.code) AS name, b.amount, b.pct_chg
                 FROM bars_daily b JOIN instruments i ON i.code = b.code
                WHERE b.trade_date=? AND i.type='stock' AND b.amount>0
                ORDER BY b.amount DESC LIMIT ?""",
            (trade_date, top),
        )
    ]
    rank = percentile("top100_pct")
    stance = "集中" if (rank or 0) >= 80 else ("分散" if (rank if rank is not None else 100) <= 20 else "中性")
    return {
        "ok": True,
        "trade_date": today["trade_date"],
        "total": round(today["total"] / 1e12, 2),          # 万亿
        "count": today["count"],
        "top100_pct": today["top100_pct"],
        "top100_rank": rank,
        "top10pct_pct": today["top10pct_pct"],
        "hhi": today["hhi"],
        "hhi_rank": percentile("hhi"),
        "big_count": today["big_count"],
        "big_rank": percentile("big_count"),
        "stance": stance,
        "window": window,
        "top10": top10,
        "series": series[-window:],
    }


def concentration_study(conn, cfg: dict, days: int = 240, horizon: int = 20) -> dict:
    """集中度分档：把每个交易日的集中度按分位分三档，看之后 horizon 日沪深300 的涨跌。

    为什么用沪深300：集中度是**全市场**的现象，要配一个全市场的刻度，而沪深300 是现成的、
    每天都有的序列，口径稳定。（全市场等权要重算几百万行——等这个指标先证明自己有信息量再说。）

    这一步的意义：如果三档的后续涨跌没有差别，那它就只是个"读法"，**不许进决策**。
    """
    from . import stats as stats_mod

    conf = ((cfg or {}).get("market") or {}).get("concentration") or {}
    big_amount = float(conf.get("big_amount", 5_000_000_000))
    bench = BENCHMARK
    closes = [
        dict(row)
        for row in db.query(
            conn,
            """SELECT trade_date, COALESCE(close_adj, close) AS close FROM bars_daily
                WHERE code=? ORDER BY trade_date""",
            (bench,),
        )
    ]
    if len(closes) < horizon + 30:
        return {"ok": False, "message": "基准指数历史不够，算不出分档"}
    index_of = {row["trade_date"]: position for position, row in enumerate(closes)}

    daily = []
    for row in closes[-(days + horizon + 1):]:
        entry = _concentration_of_day(conn, row["trade_date"], big_amount)
        position = index_of.get(row["trade_date"])
        if not entry or position is None or position + horizon >= len(closes):
            continue
        forward = (float(closes[position + horizon]["close"]) / float(row["close"]) - 1) * 100
        daily.append({"trade_date": row["trade_date"], "top100_pct": entry["top100_pct"],
                      "forward": forward})
    if len(daily) < 40:
        return {"ok": False, "message": f"样本只有 {len(daily)} 天，先跑更多历史"}

    values = sorted(item["top100_pct"] for item in daily)
    low_edge = values[int(len(values) * 0.33)]
    high_edge = values[int(len(values) * 0.67)]
    buckets: dict[str, dict[str, list[float]]] = {"分散": {}, "中性": {}, "集中": {}}
    for item in daily:
        label = ("分散" if item["top100_pct"] <= low_edge
                 else "集中" if item["top100_pct"] >= high_edge else "中性")
        buckets[label].setdefault(item["trade_date"], []).append(item["forward"])
    tests = sum(1 for values_ in buckets.values() if values_)
    rows = []
    for label in ("分散", "中性", "集中"):
        stats = stats_mod.daily_mean_stats(buckets[label])
        rows.append({
            "bucket": label,
            "days": stats["days"],
            "mean": round(stats["mean"], 2) if stats["mean"] is not None else None,
            "t": stats["t"],
            "p_adj": stats_mod.sidak_adjust(stats_mod.two_sided_p(stats["t"]), tests),
        })
    contrast = None
    low_days, high_days = buckets["分散"], buckets["集中"]
    if len(low_days) >= 5 and len(high_days) >= 5:
        low_means = [statistics.mean(values_) for values_ in low_days.values()]
        high_means = [statistics.mean(values_) for values_ in high_days.values()]
        gap = statistics.mean(high_means) - statistics.mean(low_means)
        se = (statistics.pstdev(high_means) ** 2 / len(high_means)
              + statistics.pstdev(low_means) ** 2 / len(low_means)) ** 0.5
        t_value = round(gap / se, 2) if se > 0 else None
        contrast = {"label": "集中 − 分散", "gap": round(gap, 2), "t": t_value,
                    "p_adj": stats_mod.sidak_adjust(stats_mod.two_sided_p(t_value), tests)}
    return {"ok": True, "days": len(daily), "horizon": horizon, "benchmark": bench,
           "buckets": rows, "contrast": contrast,
           "edges": {"low": low_edge, "high": high_edge}}


def record(conn, cfg: dict, trade_date: str | None = None) -> dict:
    """把当天的资金去向写进 `market_flow`（一天一行）。

    为什么要存：页面上算的是**当天**，而"宽基净申购的日子后面行情好不好"这种问题
    必须有历史序列才能回答。ETF 申赎只有 20 来个交易日的历史，所以现在是开头——
    每天日终自动写一行，样本自然会长出来。
    """
    snap = snapshot(conn, cfg, trade_date)
    if not snap.get("ok"):
        return {"ok": False, "message": snap.get("message", "算不出资金去向")}
    amount = snap.get("amount") or {}
    margin = snap.get("margin") or {}
    concentration = snap.get("concentration") or {}
    index_pct = None
    row = db.query_one(
        conn,
        "SELECT pct_chg FROM bars_daily WHERE code=? AND trade_date=?",
        (BENCHMARK, snap["trade_date"]),
    )
    if row:
        index_pct = row["pct_chg"]
    payload = {
        "trade_date": snap["trade_date"],
        "broad_etf_inflow": (snap.get("broad_etf") or {}).get("inflow"),
        "sector_etf_inflow": (snap.get("sector_etf") or {}).get("inflow"),
        "etf_covered": snap.get("covered"),
        "amount": amount.get("amount"),
        "amount_ratio": amount.get("amount_ratio"),
        "margin_delta": margin.get("delta"),
        "margin_balance": margin.get("balance"),
        "top100_pct": concentration.get("top100_pct"),
        "hhi": concentration.get("hhi"),
        "big_count": concentration.get("big_count"),
        "index_pct": index_pct,
        "updated_at": db.now_iso(),
    }
    db.upsert_rows(conn, "market_flow", [payload], ["trade_date"])
    return {"ok": True, "trade_date": snap["trade_date"], "row": payload}


def backfill(conn, cfg: dict, days: int = 240, verbose: bool = False) -> dict:
    """回填 `market_flow`：能算多少算多少。

    成交额与集中度能回溯到有日线的地方（本地约 500 个交易日），
    ETF 申赎只有 20 来个交易日（交易所按日披露的那段），融资余额 20 天。
    早期那些行的对应列**留空**——空着是"不知道"，填 0 是"没变化"，两回事。
    """
    dates = [
        row["trade_date"]
        for row in db.query(
            conn,
            """SELECT DISTINCT trade_date FROM bars_daily ORDER BY trade_date DESC LIMIT ?""",
            (int(days),),
        )
    ]
    written = 0
    for day in sorted(dates):
        result = record(conn, cfg, day)
        if result.get("ok"):
            written += 1
            if verbose and written % 50 == 0:
                print(f"    已回填 {written}/{len(dates)}", flush=True)
    return {"ok": True, "written": written, "days": len(dates)}


def series(conn, days: int = 60) -> list[dict]:
    """读 `market_flow` 的最近 N 行（页面与回测都用它）。"""
    rows = [
        dict(row)
        for row in db.query(
            conn, "SELECT * FROM market_flow ORDER BY trade_date DESC LIMIT ?", (int(days),)
        )
    ]
    return list(reversed(rows))


def flow_study(conn, cfg: dict, days: int = 60, horizon: int = 20) -> dict:
    """资金面分档：把 `market_flow` 的历史按条件分档，看之后 horizon 日沪深300 的涨跌。

    三个条件各自成一组（不做二维交叉——样本本来就少，交叉只会让每格都不够）：
      · 宽基 ETF：净申购 / 净赎回；
      · 行业 ETF：净申购 / 净赎回；
      · 集中度：高（≥70 分位）/ 低（≤30 分位）。
    样本不足时**明说"还算不出结论"**，不硬凑——这一层的全部意义就是等样本。
    """
    from . import stats as stats_mod

    rows = series(conn, days)
    closes = [
        dict(row)
        for row in db.query(
            conn,
            """SELECT trade_date, COALESCE(close_adj, close) AS close FROM bars_daily
                WHERE code=? ORDER BY trade_date""",
            (BENCHMARK,),
        )
    ]
    index_of = {row["trade_date"]: position for position, row in enumerate(closes)}

    # 只有"后面确实有 horizon 根 K 线"的交易日才算样本；而且分位要在**这批样本内**算——
    # 拿全部行算分位、再只对早期行取结果，会得到一个荒谬的偏样本（最新那些高集中度日
    # 还没长出前瞻收益，于是"集中度低"那组只剩两三天，跑出 t=−11 这种假显著）。
    eligible = []
    for row in rows:
        position = index_of.get(row["trade_date"])
        if position is None or position + horizon >= len(closes):
            continue
        forward = (float(closes[position + horizon]["close"]) / float(closes[position]["close"]) - 1) * 100
        eligible.append({**row, "forward": forward})
    percentiles = sorted(row["top100_pct"] for row in eligible if row.get("top100_pct") is not None)

    def rank_of(value: float) -> float | None:
        if not percentiles:
            return None
        return sum(1 for item in percentiles if item < value) / len(percentiles) * 100

    daily: dict[str, dict[str, list[float]]] = {}
    counts = {"宽基净申购": 0, "宽基净赎回": 0, "行业净申购": 0, "行业净赎回": 0,
              "集中度高": 0, "集中度低": 0}
    for row in eligible:
        forward = row["forward"]
        labels = []
        if row.get("broad_etf_inflow") is not None:
            labels.append("宽基净申购" if row["broad_etf_inflow"] > 0 else "宽基净赎回")
        if row.get("sector_etf_inflow") is not None:
            labels.append("行业净申购" if row["sector_etf_inflow"] > 0 else "行业净赎回")
        if row.get("top100_pct") is not None:
            rank = rank_of(float(row["top100_pct"]))
            if rank is not None and rank >= 70:
                labels.append("集中度高")
            elif rank is not None and rank <= 30:
                labels.append("集中度低")
        for label in labels:
            daily.setdefault(label, {}).setdefault(row["trade_date"], []).append(forward)
            counts[label] = counts.get(label, 0) + 1

    tests = sum(1 for values_ in daily.values() if values_)
    buckets = []
    for label, values_ in daily.items():
        stats = stats_mod.daily_mean_stats(values_)
        buckets.append({
            "bucket": label,
            "days": stats["days"],
            "samples": len(values_),
            "mean": round(stats["mean"], 2) if stats["mean"] is not None else None,
            "t": stats["t"],
            "p_adj": stats_mod.sidak_adjust(stats_mod.two_sided_p(stats["t"]), tests),
        })
    buckets.sort(key=lambda item: -(item["mean"] or -99))
    return {"ok": True, "horizon": horizon, "days": len(rows), "buckets": buckets,
            "counts": counts,
            "enough": all(item["days"] >= 20 for item in buckets) if buckets else False}
