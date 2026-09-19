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


def _etf_flows(conn, cfg: dict, dates: list[str]) -> tuple[dict, list[str], int]:
    """把最近两个交易日的 ETF 份额变化换成净流入（亿元），按宽基 / 行业主题分开。

    为什么只算沪市："份额是 T+1 披露的"，而深交所那个接口只给"最新份额"，
    补不出历史——拿它算"较前一日"会得到一串 0.00%，那是假的。
    """
    if len(dates) < 2:
        return {}, [], 0
    today, prev = dates[0], dates[1]
    rows = [
        dict(row)
        for row in db.query(
            conn,
            """SELECT s.code, s.trade_date, s.shares, s.nav, b.close, i.type
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
    unpriced: list[str] = []
    for code, pair in by_code.items():
        now, before = pair.get(today), pair.get(prev)
        if not now or not before or not before.get("shares") or not now.get("shares"):
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
    flows = {
        kind: {"inflow": round(value / 1e8, 2), "count": counts.get(kind, 0)}
        for kind, value in totals.items()
    }
    return flows, unpriced, len(by_code)


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
    """融资余额变化（亿元）。交易所 T+1 披露，所以按**有数据的交易日**往前找两天。"""
    dates = [
        row["trade_date"]
        for row in db.query(
            conn,
            """SELECT DISTINCT trade_date FROM margin WHERE trade_date<=?
                ORDER BY trade_date DESC LIMIT 2""",
            (trade_date,),
        )
    ]
    if not dates:
        return {}
    totals: dict[str, float] = {}
    for day in dates:
        rows = [
            dict(row)
            for row in db.query(
                conn,
                """SELECT market, financing_balance, securities_lending, data_date
                     FROM margin WHERE trade_date=?""",
                (day,),
            )
        ]
        total = sum(float(row.get("financing_balance") or 0) + float(row.get("securities_lending") or 0)
                    for row in rows)
        if total:
            totals[day] = total
    ordered = sorted(totals)
    if not ordered:
        return {}
    latest = ordered[-1]
    info = {
        "trade_date": latest,
        "balance": round(totals[latest] / 1e12, 3),          # 万亿
        "data_date": db.query_one(
            conn, "SELECT MAX(data_date) AS d FROM margin WHERE trade_date=?", (latest,))["d"],
    }
    if len(ordered) >= 2:
        info["delta"] = round((totals[latest] - totals[ordered[-2]]) / 1e8, 2)   # 亿元
        info["prev_date"] = ordered[-2]
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
    flows, unpriced, covered = _etf_flows(conn, cfg, dates)
    payload = {
        "ok": True,
        "trade_date": trade_date,
        "prev_date": dates[1] if len(dates) > 1 else None,
        "broad_etf": flows.get("broad_etf", {"inflow": None, "count": 0}),
        "sector_etf": flows.get("sector_etf", {"inflow": None, "count": 0}),
        "amount": _amount_context(conn, trade_date),
        "margin": _margin_delta(conn, trade_date),
        "unpriced": unpriced[:10],
        "covered": covered,
        "regime": regime.latest(conn, cfg, trade_date),
    }
    payload["verdict"] = verdict(payload)
    return payload


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
        flows, _, _ = _etf_flows(conn, cfg, pair)
        out.append({
            "trade_date": pair[0],
            "broad_etf": flows.get("broad_etf", {}).get("inflow"),
            "sector_etf": flows.get("sector_etf", {}).get("inflow"),
        })
    return list(reversed(out))
