"""因子准入工装：影子因子跑够天数了吗？跟现有因子重复吗？真的能预测吗？

规格 §11 的五步准入里，第 2~4 步需要一个能出数的工具，否则"相关性 <0.7""影子运行 20 天"
这些条件永远只是文档里的一句话。这个模块回答三件事：

  1. 样本够不够：有多少个交易日的有效数据
  2. 是不是重复指标：与已生效因子的相关系数（>0.7 视为重复）
  3. 有没有独立贡献：信息系数 IC —— 因子值与之后 5/20 个交易日收益的相关性

IC 和相关性都用手写皮尔逊，不引 numpy；样本不足 10 个点时不给结论，避免拿噪声当信号。
"""

from __future__ import annotations

from . import db, review

MIN_POINTS = 10
SHADOW_DAYS = 20
DUPLICATE_CORR = 0.7


def _pearson(pairs: list[tuple[float, float]]) -> float | None:
    count = len(pairs)
    if count < MIN_POINTS:
        return None
    mean_x = sum(pair[0] for pair in pairs) / count
    mean_y = sum(pair[1] for pair in pairs) / count
    cov = sum((pair[0] - mean_x) * (pair[1] - mean_y) for pair in pairs)
    var_x = sum((pair[0] - mean_x) ** 2 for pair in pairs)
    var_y = sum((pair[1] - mean_y) ** 2 for pair in pairs)
    if var_x <= 0 or var_y <= 0:
        return None
    return round(cov / (var_x * var_y) ** 0.5, 4)


def _factor_series(conn) -> dict[str, dict[tuple[str, str], float]]:
    """factor_id → {(code, trade_date): 归一值}。"""
    series: dict[str, dict[tuple[str, str], float]] = {}
    for row in db.query(
        conn,
        """SELECT code, trade_date, factor_id, normalized_score FROM factor_contributions
            WHERE normalized_score IS NOT NULL""",
    ):
        series.setdefault(row["factor_id"], {})[(row["code"], row["trade_date"])] = row["normalized_score"]
    return series


def _forward_lookup(conn, horizon: int) -> dict[tuple[str, str], float]:
    """(code, trade_date) → 之后第 horizon 个交易日的收益。"""
    lookup: dict[tuple[str, str], float] = {}
    for row in db.query(conn, "SELECT DISTINCT code FROM bars_daily"):
        code = row["code"]
        bars = [dict(item) for item in db.query(
            conn, "SELECT trade_date, close FROM bars_daily WHERE code=? ORDER BY trade_date", (code,))]
        dates = [bar["trade_date"] for bar in bars]
        for day in dates:
            value = review.forward_return(bars, dates, day, horizon)
            if value is not None:
                lookup[(code, day)] = value
    return lookup


def factor_stats(conn, cfg: dict) -> list[dict]:
    """每个因子一份体检报告。"""
    registry = db.query(conn, "SELECT * FROM factor_registry")
    series = _factor_series(conn)
    forward = {horizon: _forward_lookup(conn, horizon) for horizon in (5, 20)}
    stats: list[dict] = []

    for factor in registry:
        factor_id = factor["factor_id"]
        values = series.get(factor_id, {})
        days = len({date for _, date in values})
        dates = sorted({date for _, date in values})
        entry = {
            "factor_id": factor_id,
            "name": factor["name"] or factor_id,
            "layer": factor["layer"],
            "role": factor["role"],
            "category": factor["category"],
            "status": factor["status"],
            "weight": factor["weight"],
            "days": days,
            "first_date": dates[0] if dates else None,
            "last_date": dates[-1] if dates else None,
        }
        for horizon in (5, 20):
            pairs = [
                (value, forward[horizon][key])
                for key, value in values.items()
                if key in forward[horizon]
            ]
            entry[f"ic{horizon}"] = _pearson(pairs)
            entry[f"ic{horizon}_n"] = len(pairs)

        # 与已生效因子的最大相关性（影子因子才关心，但全都算出来，方便一眼看冗余）
        worst: tuple[float, str] | None = None
        for other in registry:
            other_id = other["factor_id"]
            if other_id == factor_id or other_id not in series:
                continue
            common = set(values) & set(series[other_id])
            pairs = [(values[key], series[other_id][key]) for key in common]
            corr = _pearson(pairs)
            if corr is None:
                continue
            if worst is None or abs(corr) > abs(worst[0]):
                worst = (corr, other_id)
        entry["max_corr"] = worst[0] if worst else None
        entry["max_corr_with"] = worst[1] if worst else None
        entry["verdict"] = _verdict(entry)
        stats.append(entry)
    return stats


def _verdict(entry: dict) -> str:
    if entry["days"] < SHADOW_DAYS:
        return f"样本不足（{entry['days']}/{SHADOW_DAYS} 个交易日）"
    if entry["max_corr"] is not None and abs(entry["max_corr"]) >= DUPLICATE_CORR:
        return f"与 {entry['max_corr_with']} 高度相关（{entry['max_corr']:+.2f}），是重复指标"
    ic20 = entry.get("ic20")
    if ic20 is None:
        return "数据不足，算不出 IC"
    if abs(ic20) < 0.02:
        return f"没有独立预测力（20 日 IC {ic20:+.3f}）"
    if ic20 > 0:
        return f"有正向预测力（20 日 IC {ic20:+.3f}），可以进入转正评估"
    return f"方向是反的（20 日 IC {ic20:+.3f}），先别用"


def report(stats: list[dict]) -> str:
    lines = ["===== 因子台账体检 =====", ""]
    lines.append(f"判断标准：影子运行 ≥ {SHADOW_DAYS} 个交易日、与现有因子相关性 < {DUPLICATE_CORR}、"
                 f"IC 绝对值 ≥ 0.02")
    lines.append("")
    for entry in sorted(stats, key=lambda item: (item["status"] != "shadow", item["factor_id"])):
        tag = {"active": "生效", "shadow": "影子", "candidate": "候选", "retired": "退役"}.get(
            entry["status"], entry["status"])
        corr = f"{entry['max_corr']:+.2f}（{entry['max_corr_with']}）" if entry["max_corr"] is not None else "—"
        ic5 = f"{entry['ic5']:+.3f}" if entry["ic5"] is not None else "—"
        ic20 = f"{entry['ic20']:+.3f}" if entry["ic20"] is not None else "—"
        lines.append(f"  [{tag}] {entry['name']}（{entry['factor_id']}，{entry['category']}，权重 {entry['weight']}）")
        lines.append(f"        覆盖 {entry['days']} 个交易日（{entry['first_date']} ~ {entry['last_date']}）"
                     f"｜IC5 {ic5}｜IC20 {ic20}｜最大相关 {corr}")
        lines.append(f"        → {entry['verdict']}")
    lines.append("")
    lines.append("说明：IC 是因子值与之后收益的相关性，只说明「有没有关系」，不代表能赚钱；")
    lines.append("      真正的检验还是 17-参数回测 那一套（信号能不能跑赢基准）。")
    return "\n".join(lines)
