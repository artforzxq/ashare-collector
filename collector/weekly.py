"""周度环境卡：把每周手抄的四个数字，换成一次命令。

这是笔记里那张"周度环境卡_打印版.html"的自动版。它只做观察，不产生信号：
  情绪阶段（影子）/ 成交额 / ETF 净申购 / 两融变化 / 组合在险

四个字段里，20 日涨幅前三、板块成交占比趋势、新高行业分布都需要行业分类，
而项目现在只有"主板/创业板/科创板"（`instruments.board`），没有申万/中信行业。
所以 v0 先给能算的，缺的那一项**明说缺**，不拿个股涨幅冒充行业涨幅。
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from . import db, journal, mainline, notify, sentiment


def _amount_ratio(conn, trade_date: str, window: int = 60) -> float | None:
    row = db.query_one(
        conn,
        "SELECT total_amount FROM market_breadth WHERE trade_date=?",
        (trade_date,),
    )
    if not row or not row["total_amount"]:
        return None
    history = [
        float(item["total_amount"])
        for item in db.query(
            conn,
            """SELECT total_amount FROM market_breadth WHERE trade_date<?
                 AND total_amount IS NOT NULL ORDER BY trade_date DESC LIMIT ?""",
            (trade_date, window),
        )
    ]
    if len(history) < 20:
        return None
    return float(row["total_amount"]) / statistics.median(history)


def _etf_flow(conn, cfg: dict | None = None) -> dict | None:
    days = [row["trade_date"] for row in db.query(
        conn, "SELECT DISTINCT trade_date FROM etf_shares ORDER BY trade_date DESC LIMIT 2")]
    if len(days) < 2:
        return None
    today, previous = days[0], days[1]
    broad = {str(code).upper() for code in ((cfg or {}).get("market") or {}).get("broad_etfs") or []}
    rows = [
        dict(row)
        for row in db.query(
            conn,
            """SELECT code, trade_date, shares, nav FROM etf_shares
                 WHERE trade_date IN (?,?)""",
            (today, previous),
        )
    ]
    paired: dict[str, dict] = {}
    for row in rows:
        paired.setdefault(row["code"], {})[row["trade_date"]] = row
    broad_flow = sector_flow = 0.0
    counted = 0
    for code, pair in paired.items():
        if today not in pair or previous not in pair:
            continue
        now, before = pair[today], pair[previous]
        if now.get("shares") is None or before.get("shares") is None:
            continue
        price = now.get("nav") or before.get("nav") or 0
        if not price:
            continue
        value = (float(now["shares"]) - float(before["shares"])) * float(price)
        if code.upper() in broad:
            broad_flow += value
        else:
            sector_flow += value
        counted += 1
    if not counted:
        return None
    return {
        "today": today, "previous": previous, "counted": counted,
        "broad": round(broad_flow / 1e8, 2), "sector": round(sector_flow / 1e8, 2),
    }


def _margin_change(conn) -> dict | None:
    days = [row["data_date"] for row in db.query(
        conn,
        """SELECT data_date FROM margin WHERE data_date IS NOT NULL
             GROUP BY data_date ORDER BY data_date DESC LIMIT 2""")]
    if len(days) < 2:
        return None
    values = {}
    for day in days:
        row = db.query_one(conn, "SELECT SUM(total) AS total FROM margin WHERE data_date=?", (day,))
        values[day] = float(row["total"] or 0)
    return {"today": days[0], "change": round((values[days[0]] - values[days[1]]) / 1e8, 2)}


def build(conn, cfg: dict | None = None) -> dict:
    latest = sentiment.latest(conn, cfg)
    if not latest:
        return {"ok": False, "message": "还没有市场广度。先跑 python run.py breadth 或 3-每日任务"}
    trade_date = latest["trade_date"]
    stages = [
        row["sentiment_stage"] or "未定"
        for row in db.query(
            conn,
            """SELECT sentiment_stage FROM market_breadth WHERE trade_date<=?
                 ORDER BY trade_date DESC LIMIT 5""",
            (trade_date,),
        )
    ][::-1]
    result = {
        "ok": True,
        "trade_date": trade_date,
        "sentiment": latest,
        "premium": sentiment.latest_available_premium(conn, trade_date),
        "stages": stages,
        "amount_ratio": _amount_ratio(conn, trade_date),
        "etf": _etf_flow(conn, cfg),
        "margin": _margin_change(conn),
        "journal": journal.portfolio_risk(conn, cfg),
    }
    try:
        result["mainline"] = mainline.build(conn, cfg, trade_date=trade_date)
    except mainline.MainlineError as exc:
        result["mainline"] = None
        result["mainline_error"] = str(exc)
    return result


def report(data: dict) -> str:
    if not data.get("ok"):
        return f"周度环境卡：{data.get('message', '读不出来')}"
    latest = data["sentiment"]
    lines = ["===== 周度环境卡 =====",
             f"数据截至 {data['trade_date']}｜只做观察，不产生买卖信号"]
    stage = latest.get("sentiment_stage") or "未定"
    lines.append("")
    lines.append(f"情绪阶段：{stage}")
    if latest.get("sentiment_note"):
        lines.append(f"  {latest['sentiment_note']}")
    premium = data.get("premium")
    if premium:
        lines.append(f"  最近可算的次日溢价（{premium['trade_date']}）："
                     f"连板 {premium['limit_up_next_avg']:+.2f}%"
                     f"（{premium['limit_up_next_count']} 只），"
                     f"首板 {premium['first_board_next_avg']:+.2f}%"
                     if premium.get("first_board_next_avg") is not None
                     else f"  最近可算的次日溢价（{premium['trade_date']}）："
                          f"连板 {premium['limit_up_next_avg']:+.2f}%"
                          f"（{premium['limit_up_next_count']} 只）")
    if data.get("stages"):
        lines.append(f"  近 5 个交易日：{' → '.join(data['stages'])}")
    if data.get("amount_ratio") is not None:
        lines.append(f"成交额：近 60 日中位数的 {data['amount_ratio']:.2f} 倍")
    if data.get("etf"):
        etf = data["etf"]
        lines.append(f"ETF 份额净流入（{etf['today']}）：宽基 {etf['broad']:+.2f} 亿，"
                     f"行业/主题 {etf['sector']:+.2f} 亿（{etf['counted']} 只可比）")
    else:
        lines.append("ETF 份额：本地不足两个披露日，算不出净申购")
    if data.get("margin"):
        lines.append(f"两融余额变化（{data['margin']['today']}）："
                     f"{data['margin']['change']:+.2f} 亿")
    else:
        lines.append("两融余额：本地不足两个披露日，算不出变化")
    lines.append("")
    if data.get("mainline"):
        ml = data["mainline"]
        lines.append(f"行业口径：{ml['coverage']['coverage']:.1f}% 覆盖、"
                     f"{ml['coverage']['industries']} 个行业")
        lines.append("20 日涨幅前三：")
        for item in ml["industries"][:3]:
            trend = (f"{item['share_trend_pp']:+.3f}pp" if item["share_trend_pp"] is not None
                     else "趋势样本不足")
            lines.append(f"  {item['rank']}. {item['industry']} {item['ret20']:+.2f}%，"
                         f"成交占比 {trend}，扩散 {item['diffusion']:.0f}%，"
                         f"新高 {item['new_high_share']:.0f}%")
        if ml["mainlines"]:
            lines.append("主线候选：" + "、".join(item["industry"] for item in ml["mainlines"]))
        else:
            lines.append("主线候选：无（四关全过才算；没有主线时观察比动手便宜）")
        if ml["events"]:
            lines.append("事件驱动：" + "、".join(item["industry"] for item in ml["events"][:3]))
    else:
        lines.append("缺：行业分类。以下三项要等行业映射接上才能算——")
        lines.append("  · 20 日涨幅前三的行业 / 概念")
        lines.append("  · 板块成交额占全市场的 20 日趋势")
        lines.append("  · 创新高的行业分布")
        if data.get("mainline_error"):
            lines.append(f"  （{data['mainline_error']}）")
    journal_risk = data.get("journal") or {}
    if journal_risk.get("open_count"):
        lines.append("")
        lines.append(f"组合在险：{journal_risk['total_risk_pct']:.2f}% / "
                     f"上限 {journal_risk['limits']['portfolio_risk_pct']:.2f}%"
                     f"（{journal_risk['open_count']} 笔未平仓）")
        for item in journal_risk.get("breaches") or []:
            lines.append(f"  ✗ {item['kind']} {item['key']}："
                         f"{item['value']:.2f}% > {item['limit']:.2f}%")
    lines.append("")
    lines.append("三道闸门（任一不过 → 不动手）：")
    lines.append("  ① 情绪：只有发酵期才可以更积极；高潮只减、退潮不做")
    lines.append("  ② 赔率：龙头/首仓 ≥3:1，补涨 ≥4:1，且已写下失效价")
    lines.append("  ③ 仓位：单笔 ≤1%R、合计 ≤2%R，同一题材按相加算")
    return "\n".join(lines)


def _state_path(cfg: dict | None = None) -> Path:
    root = Path(((cfg or {}).get("_project_root") or "."))
    rel = (((cfg or {}).get("notify") or {}).get("weekly_state_file")
           or "data/weekly-state.json")
    return root / rel


def snapshot(data: dict) -> dict:
    ml = data.get("mainline") or {}
    return {
        "trade_date": data.get("trade_date"),
        "stage": (data.get("sentiment") or {}).get("sentiment_stage"),
        "mainlines": sorted(item["industry"] for item in (ml.get("mainlines") or [])),
        "events": sorted(item["industry"] for item in (ml.get("events") or [])),
    }


def changes(previous: dict | None, current: dict) -> list[str]:
    if not previous:
        return ["首次生成周度环境卡"]
    out = []
    if previous.get("stage") != current.get("stage"):
        out.append(f"情绪阶段 {previous.get('stage') or '—'} → {current.get('stage') or '—'}")
    before = set(previous.get("mainlines") or [])
    after = set(current.get("mainlines") or [])
    added = sorted(after - before)
    removed = sorted(before - after)
    if added:
        out.append("主线候选新增：" + "、".join(added))
    if removed:
        out.append("主线候选退出：" + "、".join(removed))
    return out


def push_if_changed(conn, cfg: dict, force: bool = False, dry_run: bool = False) -> dict:
    """只在有变化时推送；没有变化就保持沉默。"""
    data = build(conn, cfg)
    if not data.get("ok"):
        return {"sent": False, "reason": data.get("message", "周卡生成失败")}
    current = snapshot(data)
    path = _state_path(cfg)
    previous = None
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = None
    reasons = changes(previous, current)
    if not reasons and not force:
        return {"sent": False, "reason": "无变化，保持沉默", "snapshot": current}
    if not reasons:
        reasons = ["手动强制推送"]
    content = report(data) + "\n\n本次变化：\n- " + "\n- ".join(reasons)
    if dry_run:
        return {"sent": False, "dry_run": True, "content": content,
                "reasons": reasons, "snapshot": current}
    result = notify.send("周度环境变化", content, cfg)
    if result.get("ok"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"sent": bool(result.get("ok")), "result": result,
            "reasons": reasons, "snapshot": current}
