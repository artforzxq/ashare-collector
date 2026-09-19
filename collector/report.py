"""控制台简报：盘前看状态与关键带，盘后看信号与数据质量。"""

from __future__ import annotations

from . import db
from .levels import distance_pct, nearest_band

STATE_NAME = {"up": "上升趋势", "range": "震荡", "down": "下跌趋势"}


def daily_report(conn, cfg: dict, trade_date: str | None = None) -> str:
    if trade_date is None:
        row = db.query_one(conn, "SELECT MAX(trade_date) AS d FROM features_daily")
        trade_date = row["d"] if row and row["d"] else None
    if trade_date is None:
        return "还没有特征数据，先跑一次 daily。"

    lines: list[str] = []
    lines.append(f"===== 交易简报 {trade_date} =====")

    breadth = db.query_one(conn, "SELECT * FROM market_breadth WHERE trade_date=?", (trade_date,))
    if breadth:
        lines.append(
            f"市场广度：上涨 {breadth['up_count']} / 下跌 {breadth['down_count']}，"
            f"涨停 {breadth['limit_up_count']}，中位数 {breadth['median_pct_chg']}%"
        )

    lines.append("")
    lines.append("标的 / 状态 / 趋势分 / 关键带 / 机会分")
    features = db.query(conn, "SELECT * FROM features_daily WHERE trade_date=? ORDER BY code", (trade_date,))
    for row in features:
        bands = [
            dict(b)
            for b in db.query(
                conn, "SELECT * FROM levels WHERE code=? AND trade_date=? ORDER BY price_low", (row["code"], trade_date)
            )
        ]
        close = db.query_one(
            conn, "SELECT close FROM bars_daily WHERE code=? AND trade_date=?", (row["code"], trade_date)
        )
        price = close["close"] if close else None
        support = nearest_band(bands, price, "support") if price else None
        resistance = nearest_band(bands, price, "resistance") if price else None
        band_text = []
        if support and abs(distance_pct(support, price) or 99) <= 8:
            band_text.append(f"支撑 {support['price_low']:.3f}–{support['price_high']:.3f}（{distance_pct(support, price):+.2f}%）")
        if resistance and abs(distance_pct(resistance, price) or 99) <= 8:
            band_text.append(f"阻力 {resistance['price_low']:.3f}–{resistance['price_high']:.3f}（{distance_pct(resistance, price):+.2f}%）")
        # 风险层出口：仓位上限与止损位
        risk_text = ""
        try:
            cap, stop = row["position_cap"], row["stop_level"]
        except (IndexError, KeyError):
            cap = stop = None
        if cap and stop:
            risk_text = f"  ｜ 仓位上限 {cap:.0%} 止损 {stop:.3f}"
        elif cap is not None and not cap:
            risk_text = "  ｜ 风险层：0 仓"
        lines.append(
            f"  {row['code']}  {STATE_NAME.get(row['state'], row['state'])}"
            f"（持续 {row['state_days']} 日，趋势分 {row['trend_score']}）"
            + ("  " + " ｜ ".join(band_text) if band_text else "")
            + risk_text
        )

    lines.append("")
    lines.append("今日提醒")
    alerts = db.query(conn, "SELECT * FROM alerts WHERE trade_date=? ORDER BY level, code", (trade_date,))
    if not alerts:
        lines.append("  （无）")
    for alert in alerts:
        lines.append(f"  [{alert['level']}] {alert['code']} {alert['signal_type']}：{alert['message']}")

    suppressed = db.query(
        conn,
        "SELECT * FROM arbitration_log WHERE trade_date=? ORDER BY rule_applied",
        (trade_date,),
    )
    if suppressed:
        lines.append("")
        lines.append("被仲裁压制的信号（沉默或降级）")
        for item in suppressed:
            lines.append(f"  {item['code']} {item['party_a']} ← {item['rule_applied']}：{item['suppressed_signal']}")

    health = db.query(conn, "SELECT * FROM data_health WHERE run_date=? ORDER BY task", (trade_date,))
    problems = [h for h in health if h["status"] not in ("ok",)]
    if problems:
        lines.append("")
        lines.append("数据质量")
        for item in problems:
            lines.append(f"  {item['source']} {item['task']}：{item['status']} {item['error_msg'] or ''}")

    return "\n".join(lines)


def factor_report(conn, cfg: dict, trade_date: str, code: str) -> str:
    """回放某只标的当日的因子贡献，回答"结论是被谁拉过去的"。"""
    rows = db.query(
        conn,
        "SELECT * FROM factor_contributions WHERE trade_date=? AND code=? ORDER BY contribution DESC",
        (trade_date, code),
    )
    if not rows:
        return f"{code} {trade_date} 没有因子贡献记录。"
    lines = [f"===== {code} {trade_date} 因子贡献 ====="]
    for row in rows:
        mark = "" if row["weight"] else "  （观察期，不计分）"
        lines.append(
            f"  {row['factor_id']:<12} 原始 {row['raw_value']!s:<10} 归一 {row['normalized_score']!s:<8}"
            f" 权重 {row['weight']:<5} 贡献 {row['contribution']}{mark}"
        )
    feature = db.query_one(
        conn, "SELECT trend_score, state, state_days FROM features_daily WHERE trade_date=? AND code=?", (trade_date, code)
    )
    if feature:
        lines.append(f"  → 趋势分 {feature['trend_score']}，状态 {feature['state']}（持续 {feature['state_days']} 日）")
    return "\n".join(lines)


# ---------------------------------------------------------------- HTML 版简报
#
# 为什么单独做一份 HTML：微信里等宽文本会换行错位，"支撑 66.74–69.39" 这种靠空格对齐的
# 排版一进手机就散架。推送要好看，就得用表格/徽章这套东西，而且样式必须写成**行内**——
# 微信会把 <style> 标签和 class 都剥掉，只有 style="..." 能活下来。

def _esc(text) -> str:
    import html

    return html.escape(str(text if text is not None else ""))


def _badge(text: str, color: str, bg: str) -> str:
    return (f'<span style="display:inline-block;padding:1px 6px;border-radius:3px;'
            f'font-size:11px;color:{color};background:{bg}">{_esc(text)}</span>')


def _state_badge(state: str) -> str:
    table = {"up": ("上升", "#d92b2b", "#fdecea"),
             "down": ("下跌", "#0f9f74", "#e7f6ee"),
             "range": ("震荡", "#64748b", "#eef1f6")}
    label, color, bg = table.get(state, (state or "—", "#64748b", "#eef1f6"))
    return _badge(label, color, bg)


def _pct_text(value) -> tuple[str, str]:
    """(文本, 颜色)：A 股口径，红涨绿跌。"""
    if value is None:
        return "—", "#94a3b8"
    text = f"{value:+.2f}%"
    return text, ("#d92b2b" if value > 0 else "#0f9f74" if value < 0 else "#64748b")


def newstock_section(conn, cfg: dict, trade_date: str) -> str:
    """新股与次新那一段：今日新上市 + 次新里值得提一句的。"""
    from . import newstock as newstock_mod

    try:
        listed = newstock_mod.today_listings(conn, trade_date)
        picks = newstock_mod.highlights(conn, cfg, trade_date)
    except Exception:
        return ""

    rows: list[str] = []
    if listed:
        names = "　".join(
            f"{_esc(item['name'])}（{_esc(item['code'])}·{_esc(item['board'])}）" for item in listed[:8])
        rows.append(f'<div style="margin:8px 0 0"><b>今日新上市</b>　{names}</div>')
    if picks:
        lines = []
        for item in picks:
            since = item.get("since_list_pct")
            text, color = _pct_text(since)
            mark = " 🔔" if item.get("alerted") else ""
            lines.append(
                f'<div style="padding:4px 0;border-top:1px solid #eef1f6">'
                f'<b>{_esc(item["name"])}</b> '
                f'<span style="color:#94a3b8;font-size:12px">{_esc(item["code"])}·{_esc(item["board"])}'
                f'·上市 {item["trading_days"]} 个交易日</span>'
                f'<span style="float:right;color:{color};font-size:13px">{text}</span>{mark}</div>')
        rows.append(
            '<div style="margin:10px 0 0"><b>次新关注</b>'
            '<span style="color:#94a3b8;font-size:12px">　按"今天有提醒 → 上市以来强度"排</span></div>'
            + "".join(lines))
    if not rows:
        return ""
    return (
        '<div style="margin-top:14px;padding:10px 12px;border:1px solid #e4e8ef;border-radius:8px">'
        '<div style="font-size:13px;font-weight:600;margin-bottom:2px">新股与次新</div>'
        '<div style="color:#94a3b8;font-size:11.5px">它们进不了筛选池（历史不够长），单独盯着看</div>'
        + "".join(rows) + "</div>"
    )


def support_section(conn, cfg: dict, trade_date: str) -> str:
    """疑似托底那一段：**只在有命中时才出现**（默认沉默，不打扰）。

    份额是 T+1 披露的，所以这段话说的永远是"上一个交易日有没有人进场"。
    """
    from . import support as support_mod

    try:
        result = support_mod.scan(conn, cfg, trade_date)
    except Exception:
        return ""
    if not result.get("ok") or not result.get("hits"):
        return ""
    hits = result["hits"]
    total = result["net_inflow"] / 1e8
    lines = []
    for item in hits[:4]:
        lines.append(
            '<div style="padding:4px 0;border-top:1px solid #eef1f6">'
            f'<b>{_esc(item["code"])}</b>'
            f'<span style="color:#94a3b8;font-size:12px">　份额 {item["shares_pct"]:+.2f}%'
            f'　成交额 {item["amount_z"] if item["amount_z"] is not None else str(item.get("amount_ratio")) + "×"}</span>'
            f'<span style="float:right;color:#d92b2b;font-size:13px">'
            f'净流入 {(item["inflow"] or 0) / 1e8:.1f} 亿</span></div>')
    return (
        '<div style="margin-top:14px;padding:10px 12px;border:1px solid #e4e8ef;border-radius:8px">'
        f'<div style="font-size:13px;font-weight:600">疑似托底 · {_esc(result["level"])}</div>'
        f'<div style="color:#94a3b8;font-size:11.5px">{len(hits)} 只宽基 ETF 份额净流入且放量，'
        f'合计 {total:.1f} 亿元（份额 T+1 披露，属事后信号）</div>'
        + "".join(lines) + "</div>"
    )


def daily_html(conn, cfg: dict, trade_date: str | None = None) -> str:
    """手机推送用的 HTML 简报：标的表 + 提醒 + 新股次新 + 数据质量。"""
    from .levels import distance_pct, nearest_band

    if trade_date is None:
        row = db.query_one(conn, "SELECT MAX(trade_date) AS d FROM features_daily")
        trade_date = row["d"] if row and row["d"] else None
    if trade_date is None:
        return "<div>还没有特征数据，先跑一次 3-每日任务</div>"

    parts: list[str] = [
        '<div style="font:14px/1.7 -apple-system,BlinkMacSystemFont,\'PingFang SC\',sans-serif;color:#0f172a">',
        f'<div style="font-size:16px;font-weight:600">A 股简报 · {_esc(trade_date)}</div>',
        '<div style="color:#94a3b8;font-size:11.5px;margin-bottom:10px">'
        '结论由本地确定性代码算出，模型不参与</div>',
    ]

    breadth = db.query_one(conn, "SELECT * FROM market_breadth WHERE trade_date=?", (trade_date,))
    if breadth:
        median, mcolor = _pct_text(breadth["median_pct_chg"])
        parts.append(
            '<div style="padding:8px 10px;background:#fafbfd;border-radius:8px;font-size:12.5px">'
            f'市场广度　上涨 <b style="color:#d92b2b">{breadth["up_count"]}</b> / '
            f'下跌 <b style="color:#0f9f74">{breadth["down_count"]}</b>　'
            f'中位数 <b style="color:{mcolor}">{median}</b>　'
            f'<span style="color:#94a3b8">样本 {breadth["coverage"]} 只</span></div>')

    features = db.query(conn, "SELECT * FROM features_daily WHERE trade_date=? ORDER BY code", (trade_date,))
    if features:
        parts.append('<div style="margin-top:12px;font-size:13px;font-weight:600">自选标的</div>')
        parts.append('<div style="border:1px solid #e4e8ef;border-radius:8px;margin-top:6px">')
        for row in features:
            bands = [dict(b) for b in db.query(
                conn, "SELECT * FROM levels WHERE code=? AND trade_date=? ORDER BY price_low",
                (row["code"], trade_date))]
            close = db.query_one(
                conn, "SELECT close, pct_chg FROM bars_daily WHERE code=? AND trade_date=?",
                (row["code"], trade_date))
            price = close["close"] if close else None
            support = nearest_band(bands, price, "support") if price else None
            resistance = nearest_band(bands, price, "resistance") if price else None
            band_bits = []
            if support and abs(distance_pct(support, price) or 99) <= 8:
                band_bits.append(f'支撑 {support["price_low"]:.3f}–{support["price_high"]:.3f}'
                                 f'（{distance_pct(support, price):+.2f}%）')
            if resistance and abs(distance_pct(resistance, price) or 99) <= 8:
                band_bits.append(f'阻力 {resistance["price_low"]:.3f}–{resistance["price_high"]:.3f}'
                                 f'（{distance_pct(resistance, price):+.2f}%）')
            cap, stop = row["position_cap"], row["stop_level"]
            risk = (f'上限 {cap:.0%} · 止损 {stop:.3f}' if cap and stop
                    else '风险层：0 仓' if cap is not None and not cap else '')
            chg_text, chg_color = _pct_text(close["pct_chg"] if close else None)
            name = db.query_one(conn, "SELECT name FROM instruments WHERE code=?", (row["code"],))
            label = (name["name"] if name and name["name"] else row["code"])
            parts.append(
                '<div style="padding:8px 10px;border-top:1px solid #eef1f6">'
                f'<div><b>{_esc(label)}</b> '
                f'<span style="color:#94a3b8;font-size:11.5px">{_esc(row["code"])}</span>'
                f'<span style="float:right;font-size:12.5px">'
                f'{_state_badge(row["state"])} '
                f'<span style="color:{chg_color}">{chg_text}</span></span></div>'
                f'<div style="color:#475569;font-size:12px;margin-top:2px">'
                f'趋势分 {row["trend_score"]}　' + "　".join(band_bits) +
                (f'　{risk}' if risk else '') + '</div></div>')
        parts.append("</div>")

    alerts = db.query(conn, "SELECT * FROM alerts WHERE trade_date=? ORDER BY level, code", (trade_date,))
    parts.append('<div style="margin-top:12px;font-size:13px;font-weight:600">今日提醒</div>')
    if alerts:
        parts.append('<div style="border:1px solid #e4e8ef;border-radius:8px;margin-top:6px">')
        for alert in alerts:
            color, bg = (("#d92b2b", "#fdecea") if alert["level"] == "P0"
                         else ("#b45309", "#fff7ed") if alert["level"] == "P1" else ("#64748b", "#eef1f6"))
            parts.append(
                '<div style="padding:7px 10px;border-top:1px solid #eef1f6">'
                f'{_badge(alert["level"], color, bg)} <b>{_esc(alert["code"])}</b>　'
                f'{_esc(alert["message"])}</div>')
        parts.append("</div>")
    else:
        parts.append('<div style="color:#94a3b8;font-size:12.5px;margin-top:4px">'
                     '无（默认沉默：没有值得打扰的信号就不打扰）</div>')

    suppressed = db.query(conn, "SELECT * FROM arbitration_log WHERE trade_date=?", (trade_date,))
    if suppressed:
        items = "；".join(f'{_esc(row["code"])} {_esc(row["party_a"])}'
                          f'←{_esc(row["rule_applied"])}' for row in suppressed[:5])
        parts.append(f'<div style="margin-top:10px;color:#64748b;font-size:12px">'
                     f'被仲裁压制的信号：{items}</div>')

    parts.append(newstock_section(conn, cfg, trade_date))

    support = support_section(conn, cfg, trade_date)
    if support:
        parts.append(support)

    health = [h for h in db.query(conn, "SELECT * FROM data_health WHERE run_date=?", (trade_date,))
              if h["status"] != "ok"]
    if health:
        items = "；".join(f'{_esc(h["source"])} {_esc(h["task"])} {_esc(h["status"])}' for h in health[:4])
        parts.append(f'<div style="margin-top:10px;color:#b45309;font-size:12px">数据质量：{items}</div>')

    parts.append('<div style="margin-top:12px;color:#cbd5e1;font-size:11px">'
                 '本地系统自动推送 · 想看细节打开看盘页面</div>')
    parts.append("</div>")
    return "".join(parts)
