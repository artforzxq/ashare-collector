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
# 同一个交易日至少要有这么多只标的，才有意义地减掉"当日全体均值"（见 _forward_lookup）
MIN_CROSS_SECTION = 3


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
    """(code, trade_date) → 之后第 horizon 个交易日的**超额**收益（减掉当日全体均值）。

    为什么减这一下：IC 要回答的是"这个因子有没有选股能力"，而原始收益里混着大盘涨跌——
    行情好的时候什么因子都"有效"。减掉当日全体均值之后，IC 和回测里的"净超额"才是同一把尺子；
    不减的话，同一个因子可能在台账里显示正 IC、在换手率研究里显示负超额（这不矛盾，是口径不同）。
    """
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

    per_day: dict[str, list[float]] = {}
    for (_, day), value in lookup.items():
        per_day.setdefault(day, []).append(value)
    # 当日样本太少（只盯一两只票）时，"减当日均值"会把所有值压成 0，IC 直接没法算。
    # 这种情况退回原始收益——口径不如超额干净，但至少还能看出方向；报告里会写明基准。
    means = {
        day: (sum(values) / len(values) if len(values) >= MIN_CROSS_SECTION else None)
        for day, values in per_day.items()
    }
    return {
        key: (value - means[key[1]] if means.get(key[1]) is not None else value)
        for key, value in lookup.items()
    }


def _partial_corr(triples: list[tuple[float, float, float]]) -> float | None:
    """剔掉第三个变量之后，前两个变量还剩多少相关（偏相关）。

    triples = [(因子值, 前瞻收益, 已有因子值)]：分别把因子值和收益对"已有因子"做一元回归，
    取残差再求相关。为什么需要它：相关系数 0.65 就已经高度重复了（实测 ma_slope 与 ma_align
    就是 +0.65），单看 IC 会把"换个名字的同一个因子"当成新信息。
    局限写在明处：只剔了相关性最高的那**一个**，不是全量正交化——够筛重复，不够声称完全独立。
    """
    if len(triples) < 10:
        return None
    xs = [item[0] for item in triples]
    ys = [item[1] for item in triples]
    zs = [item[2] for item in triples]
    n = len(triples)
    mean_x, mean_y, mean_z = sum(xs) / n, sum(ys) / n, sum(zs) / n
    var_z = sum((z - mean_z) ** 2 for z in zs)
    if var_z <= 0:
        return None
    bx = sum((x - mean_x) * (z - mean_z) for x, z in zip(xs, zs)) / var_z
    by = sum((y - mean_y) * (z - mean_z) for y, z in zip(ys, zs)) / var_z
    return _pearson([
        (x - mean_x - bx * (z - mean_z), y - mean_y - by * (z - mean_z))
        for x, y, z in zip(xs, ys, zs)
    ])


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

        # 残差 IC：把"与最相关那个因子的重复部分"剔掉之后还剩多少预测力
        entry["ic20_partial"] = None
        entry["ic20_partial_vs"] = None
        if worst is not None:
            partner = series.get(worst[1]) or {}
            triples = [
                (value, forward[20][key], partner[key])
                for key, value in values.items()
                if key in forward[20] and key in partner
            ]
            partial = _partial_corr(triples)
            if partial is not None:
                entry["ic20_partial"] = partial
                entry["ic20_partial_vs"] = worst[1]
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
    partial = entry.get("ic20_partial")
    if partial is not None and abs(partial) < 0.02:
        return (f"看着有 IC（{ic20:+.3f}），但剔掉与 {entry.get('ic20_partial_vs')} 的重复部分后"
                f"只剩 {partial:+.3f} —— 是重复指标，没有独立信息")
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
        partial = entry.get("ic20_partial")
        partial_text = (f"｜残差IC20 {partial:+.3f}（对 {entry.get('ic20_partial_vs')}）"
                        if partial is not None else "")
        ic5 = f"{entry['ic5']:+.3f}" if entry["ic5"] is not None else "—"
        ic20 = f"{entry['ic20']:+.3f}" if entry["ic20"] is not None else "—"
        lines.append(f"  [{tag}] {entry['name']}（{entry['factor_id']}，{entry['category']}，权重 {entry['weight']}）")
        lines.append(f"        覆盖 {entry['days']} 个交易日（{entry['first_date']} ~ {entry['last_date']}）"
                     f"｜IC5 {ic5}｜IC20 {ic20}｜最大相关 {corr}{partial_text}")
        lines.append(f"        → {entry['verdict']}")
    lines.append("")
    lines.append("说明：IC 是因子值与之后收益的相关性，只说明「有没有关系」，不代表能赚钱；")
    lines.append("      真正的检验还是 17-参数回测 那一套（信号能不能跑赢基准）。")
    return "\n".join(lines)
