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
