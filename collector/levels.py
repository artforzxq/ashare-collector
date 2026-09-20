"""关键点位：用成交量密集区自动聚类出支撑带与阻力带。

第一版实现：把回看窗口内的每根 K 线成交额按价格分布摊到价格分箱上，
取权重最高的若干箱、把相邻箱合并成带，再按当前位置分成支撑 / 阻力。
带宽天然存在——支撑本来就该是一段区间，而不是一个价格点。
"""

from __future__ import annotations

from typing import Sequence

from . import candles


def build_levels(
    bars: Sequence[dict],
    close: float,
    cfg: dict,
    lookback: int = 120,
) -> list[dict]:
    window = [b for b in bars[-lookback:] if b.get("low") is not None and b.get("high") is not None]
    if len(window) < 20 or not close:
        return []

    params = cfg.get("levels", {})
    bins = int(params.get("bins", 30))
    top_bins = int(params.get("top_bins", 6))
    merge_gap_pct = float(params.get("merge_gap_pct", 0.5))
    max_levels = int(params.get("max_levels", 4))
    # 一条带最宽占价格多少。以前没有这个上限，密集箱体被无脑合并，
    # 结果吐出过"阻力 1.677–1.891"这种宽 12% 的东西——那不是关键位，那是整个成交区间。
    max_width_pct = float(params.get("max_band_width_pct", 4.0))
    max_width = close * max_width_pct / 100.0

    low_price = min(float(b["low"]) for b in window)
    high_price = max(float(b["high"]) for b in window)
    if high_price <= low_price:
        return []
    step = (high_price - low_price) / bins

    weights = [0.0] * bins
    for bar in window:
        amount = float(bar.get("amount") or 0.0)
        if amount <= 0:
            continue
        bar_low, bar_high = float(bar["low"]), float(bar["high"])
        first = min(bins - 1, max(0, int((bar_low - low_price) / step)))
        last = min(bins - 1, max(0, int((bar_high - low_price) / step)))
        share = amount / (last - first + 1)
        for index in range(first, last + 1):
            weights[index] += share

    ranked = sorted(range(bins), key=lambda i: weights[i], reverse=True)[:top_bins]
    selected = sorted(ranked)

    merged: list[list[int]] = []
    for index in selected:
        if merged and index - merged[-1][-1] <= 1:
            candidate_low = low_price + merged[-1][0] * step
            candidate_high = low_price + (index + 1) * step
            if candidate_high - candidate_low <= max_width:
                merged[-1].append(index)
                continue
        merged.append([index])

    gap = close * merge_gap_pct / 100.0
    bands: list[dict] = []
    for group in merged:
        band_low = low_price + group[0] * step
        band_high = low_price + (group[-1] + 1) * step
        weight = sum(weights[i] for i in group)
        # 三种类型：价格上方是阻力、下方是支撑、**价格落在带内就是当前所处区间**。
        # 原来只有支撑/阻力两分法，价格站在区间里时会把脚下的区间误判成"阻力"。
        if band_low <= close <= band_high:
            level_type = "range"
        elif band_high < close:
            level_type = "support"
        else:
            level_type = "resistance"
        bands.append(
            {
                "level_type": level_type,
                "price_low": round(min(band_low, band_high) - 0, 4),
                "price_high": round(max(band_low, band_high), 4),
                "weight": round(weight, 0),
                "engine": "volume_profile",
            }
        )

    # 距离当前位置最近的若干带优先保留
    bands.sort(key=lambda b: abs((b["price_low"] + b["price_high"]) / 2 - close))
    bands = bands[:max_levels]

    # 吸附：把带边界与当前位置的间距做一次合并，避免细碎带
    bands.sort(key=lambda b: b["price_low"])
    compact: list[dict] = []
    for band in bands:
        if compact and band["price_low"] - compact[-1]["price_high"] <= gap:
            merged_high = max(compact[-1]["price_high"], band["price_high"])
            if merged_high - compact[-1]["price_low"] <= max_width:
                compact[-1]["price_high"] = merged_high
                compact[-1]["weight"] += band["weight"]
                compact[-1]["level_type"] = _classify(compact[-1]["price_low"], merged_high, close)
                continue
        compact.append(dict(band))
    return compact


def _classify(low: float, high: float, close: float) -> str:
    if low <= close <= high:
        return "range"
    return "support" if high < close else "resistance"


def neckline_band(bars: Sequence[dict], index: int | None = None, cfg: dict | None = None) -> dict | None:
    """把形态层的颈线变成一条"零宽带"，好跟成交量密集带一起走同一条通路。

    为什么用零宽带而不是新加一个字段：`levels` 表、页面画带、风险层取支撑全都
    已经围着"带"写好了（price_low/price_high/level_type），颈线本质上就是一个点，
    price_low == price_high 正好表达它，下游一行都不用改就能画出来、用起来。

    注意它的**位置决定角色**（和成交量带同一个规则）：在收盘下方算支撑，上方算压力。
    顶部形态的颈线（跌破之后是压力）和底部形态的颈线（站上之后是支撑）因此自动各就各位。

    离现价太远的不要：页面画带时会用它来定纵轴范围，一条在 20% 之外的旧形态颈线
    会把 K 线压成一条线。默认超过 15% 就不画（`levels.neckline_max_gap_pct`）。
    """
    if not bars:
        return None
    index = len(bars) - 1 if index is None else index
    if index < 2 or index >= len(bars):
        return None
    result = candles.analyze(bars, index)
    price = result.get("neckline")
    if not price:
        return None
    # 把"这条颈线来自顶部形态还是底部形态"一起带上（engine 列）。
    # 页面要据此标"颈线（顶）/ 颈线（底）"——光看价格在颈线上方还是下方分不出来：
    # 底部形态的颈线在**突破之前**也在价格上方。
    kind = result.get("neckline_kind") or ""
    limit = float(((cfg or {}).get("levels") or {}).get("neckline_max_gap_pct", 15.0))
    close = candles._num(bars[index].get("close"))
    if close and limit > 0 and abs(close - float(price)) / close * 100 > limit:
        return None
    return {"level_type": "neckline", "price_low": round(float(price), 4),
            "price_high": round(float(price), 4), "weight": 0.0,
            "engine": f"pattern_{kind}" if kind else "pattern"}


def nearest_band(bands: Sequence[dict], close: float, level_type: str | None = None) -> dict | None:
    candidates = [b for b in bands if level_type is None or b["level_type"] == level_type]
    if not candidates:
        return None
    return min(candidates, key=lambda b: abs((b["price_low"] + b["price_high"]) / 2 - close))


def support_for(bands: Sequence[dict], close: float) -> dict | None:
    """给"止损放哪"用的支撑参照。

    优先用价格下方的支撑带；如果价格正落在某条区间带里，
    那条带的**下沿**就是脚下的平台——这正是"前面有平台支撑"的算法表达。
    """
    support = nearest_band(bands, close, "support")
    if support:
        return support
    inside = [band for band in bands if band["level_type"] == "range" and band["price_low"] <= close]
    if inside:
        return min(inside, key=lambda band: band["price_low"])
    return None


def distance_pct(band: dict | None, close: float) -> float | None:
    if not band or not close:
        return None
    center = (band["price_low"] + band["price_high"]) / 2
    return (close - center) / close * 100.0
