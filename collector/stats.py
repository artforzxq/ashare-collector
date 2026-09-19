"""统计工具：按交易日聚合、显著性、多重检验校正。

这个模块只有一件事要强调——**同一天几千只票不是独立观测**。
它们一起涨一起跌，几乎是同一个观测被抄了几千遍。按样本算误差棒，
会窄得离谱，"显著"两个字就变得一文不值。

所以这里所有统计都先按**交易日**聚合：每天算一个均值，然后对"日"这个维度求 t 与置信区间。
参数网格（扫了多少组）和形态统计（同时看了多少个形态）都要用多重检验校正，
否则你总能在一堆噪声里挑出一个"看起来很棒"的。

不引 numpy / scipy：样本量是几百个交易日，正态近似足够。
"""

from __future__ import annotations

import math
import statistics


def daily_mean_stats(by_day: dict[str, list[float]]) -> dict:
    """把"每个交易日一组收益"汇成 均值 / 标准差 / 天数 / t 值 / 95% 置信区间。"""
    means = [statistics.mean(values) for values in by_day.values() if values]
    days = len(means)
    if days < 2:
        return {"days": days, "mean": (means[0] if means else None), "sd": None,
                "t": None, "ci95": None}
    mean = statistics.mean(means)
    # 样本标准差（除以 n-1）：这里把"交易日"当观测单位
    sd = statistics.pstdev(means) * (days / max(1, days - 1)) ** 0.5
    se = sd / (days ** 0.5)
    return {
        "days": days,
        "mean": mean,
        "sd": sd,
        "t": round(mean / se, 2) if se > 0 else None,
        "ci95": round(1.96 * se, 4),
    }


def two_sided_p(t: float | None) -> float | None:
    """双侧 p 值（正态近似）。"""
    if t is None:
        return None
    z = abs(float(t))
    return round(max(0.0, min(1.0, 1 - math.erf(z / math.sqrt(2)))), 6)


def z_for_two_sided(p: float) -> float:
    """双侧 p → 临界 z（二分求解）。"""
    low, high = 0.0, 6.0
    for _ in range(60):
        mid = (low + high) / 2
        if 1 - math.erf(mid / math.sqrt(2)) > p:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def sidak_adjust(p: float | None, tests: int) -> float | None:
    """Šidák 校正：看了 tests 个假设，至少有一个碰巧显著的概率。

    p_adj = 1 − (1 − p)^N。比 Bonferroni 稍温和，含义也更直观：
    独立重复 N 次都碰不到的概率，反过来就是"至少碰上一次"的概率。
    """
    if p is None:
        return None
    tests = max(1, int(tests))
    return round(min(1.0, 1 - (1 - p) ** tests), 4)


def crit_t(tests: int, alpha: float = 0.05) -> float:
    """在这个搜索规模下，|t| 至少要多少才算显著（Bonferroni 口径）。"""
    return round(z_for_two_sided(alpha / max(1, int(tests))), 2)
