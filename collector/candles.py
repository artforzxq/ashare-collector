"""K 线形态：把「这几根 K 线长什么样、长在哪儿」变成能计算的字段。

为什么需要这一层：均线、ATR、量能都是**平滑后**的指标，天生滞后。转折点上真正先动的
是几根 K 线的组合形态——早晨之星、看涨吞没、放量长阳、锤子、缺口。状态机要等趋势分
从 45 爬到 70 才认账，那时候第一波已经走完了。

三条纪律（做法上借鉴成熟开源形态库，规则自己写，不引入任何外部依赖）：

1. **几何只算一次**（`geometry` 原语）：形态只是原语的布尔组合，不各写一套比例，
   免得同一个"实体占比"在五个地方算出五个值。
2. **阈值集中在 `THRESHOLDS`**：要调先改这里，改完跑回归。散落在判断式里的魔数
   等于没法复现。
3. **趋势背景 + 关键位置是硬前提**：锤子和上吊线是**同一个形状**，区别只在前面是涨还是跌；
   同样一根长下影，出现在 20 日新低附近和在横盘中间，含义完全不同。所以形态名里就带着
   "出现在哪"（锤子=下跌后 / 上吊=上涨后），页面只画关键位置上的那些。

这一层只描述事实，不做预测，也不单独下结论——方向与仓位由状态层和风险层给。
"""

from __future__ import annotations

from typing import Sequence

# ---------- 阈值真相源 ----------
#
# 带 `_r` 的都是"占当日全幅（high − low）的比例"，天然跨股可比；
# 带 `_pct` 的是相对前收的百分比。改任何一个都要想清楚它同时影响筛选、提醒与回放。
THRESHOLDS = {
    # 单根几何
    "doji_body_r": 0.10,        # 十字线：实体 ≤ 全幅 10%（开≈收）
    "small_body_r": 0.34,       # 锤子/流星等"小实体"上限
    "long_body_r": 0.60,        # 长实体下限（吞没/乌云/孕线的母线）
    "shadow_mult": 2.0,         # 长影线至少是实体的几倍
    "tiny_shadow_r": 0.10,      # "另一侧几乎没影线"的上限
    "long_shadow_r": 0.30,      # 长影线本身的下限
    "close_high_r": 0.66,       # 收盘落在全幅上三分之一下界（长阳）
    "close_low_r": 0.34,        # 收盘落在全幅下三分之一上界（长阴）
    "near_eq": 0.003,           # 平底/平顶的"差不多相等"容差 0.3%
    # 长实体 + 量能
    "long_body_pct": 2.0,       # 长阳/长阴的涨跌幅下限（相对前收）
    "volume_confirm_x": 1.5,    # 放量确认：当日量 ÷ 前 20 日均量
    "engulf_volume_x": 1.2,     # 吞没类：门槛放宽一点（形态本身已经够硬）
    "shadow_volume_x": 1.3,     # 锤子/流星类
    # 组合形态
    "harami_body_r": 0.60,      # 孕线：子线实体 ≤ 母线实体 × 0.6
    "star_middle_r": 0.34,      # 星线中间那根的小实体上限
    "soldiers_body_r": 0.40,    # 红三兵/三只乌鸦：每根实体占比下限
    "soldiers_close_r": 0.60,   # 且每根收在当根全幅偏强的一侧
    # 趋势背景（用来区分"同形异位"：锤子 vs 上吊线）
    "trend_n": 10,              # 回看根数
    "trend_slope": 0.02,        # 斜率阈值 2%
    # 关键位置
    "key_lookback": 20,         # 关键位置用的区间长度（交易日）
    "key_near_ma_pct": 0.8,     # 离 MA20/MA60 多近算"贴在均线上"
    "key_edge_pct": 12.0,       # 收盘落进 20 日区间上下 12% 算"在边缘"
    "key_extreme_tol": 1.0,     # 平底/平顶要求"平"在 20 日极值附近（1% 容差）
}


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _mean(values: Sequence[float]) -> float | None:
    clean = [value for value in values if value]
    return sum(clean) / len(clean) if clean else None


# ---------- 几何原语：只算一次 ----------

def geometry(bar: dict) -> dict:
    """单根 K 线的几何量。一字线（无振幅）的比值一律记 None——0/0 不能当 0 用。

    这是全模块唯一的"这根 K 线长什么样"的出口。它只做除法，不做判断；
    所有形态都是它的布尔组合。
    """
    o, h, l, c = (_num(bar.get(k)) for k in ("open", "high", "low", "close"))
    rng = h - l
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l
    zero = rng <= 0
    return {
        "o": o, "h": h, "l": l, "c": c,
        "rng": rng, "body": body, "upper": upper, "lower": lower,
        "bull": c > o, "bear": c < o,
        "mid": (o + c) / 2.0,
        "body_r": None if zero else body / rng,
        "upper_r": None if zero else upper / rng,
        "lower_r": None if zero else lower / rng,
        "close_pos": None if zero else (c - l) / rng,
        "zero_range": zero,
    }


def trend_before(bars: Sequence[dict], index: int, n: int | None = None,
                 slope_th: float | None = None) -> str:
    """这份 bar 之前是涨、跌还是横着（返回 up / down / flat）。

    为什么形态层非要趋势：锤子和上吊线的形状一模一样，流星和倒锤星也是。
    不看前面那一段，这两种形态就只能算一个——而它们的含义正好相反。
    """
    n = int(n or THRESHOLDS["trend_n"])
    slope_th = float(THRESHOLDS["trend_slope"] if slope_th is None else slope_th)
    if index < n:
        return "flat"                       # 历史不够就不给趋势结论
    window = [_num(bars[i].get("close")) for i in range(index - n, index)]
    half = n // 2
    first = _mean(window[:half])
    last = _mean(window[half:])
    if not first:
        return "flat"
    slope = (last - first) / first
    if slope > slope_th:
        return "up"
    if slope < -slope_th:
        return "down"
    return "flat"


# ---------- 关键位置：形态只有长在这里才值得看 ----------
#
# 位置比形态更重要。同样一根锤子，出现在 20 日新低附近的支撑上，和出现在一段没意义的
# 横盘中间，含义完全不同。所以页面只画"关键位置的形态"、提醒也只在关键位置响。
#
# 判定只用当日及之前的数据（前 20 日高/低、当日均线），不用未来函数；
# 分型天然要右边一根才能确认，所以最近一根永远不算分型（这是对的）。

def key_position(bars: Sequence[dict], index: int, lookback: int | None = None,
                 near_ma_pct: float | None = None,
                 edge_pct: float | None = None) -> dict:
    """这根 K 线是不是落在关键位置（区间边缘 / 均线附近 / 20 日极值分型）。"""
    lookback = int(lookback or THRESHOLDS["key_lookback"])
    near_ma_pct = float(THRESHOLDS["key_near_ma_pct"] if near_ma_pct is None else near_ma_pct)
    edge_pct = float(THRESHOLDS["key_edge_pct"] if edge_pct is None else edge_pct)
    empty = {"key": False, "reasons": [], "prior_high": None, "prior_low": None}
    if index < 1 or index >= len(bars):
        return empty
    bar = bars[index]
    close, low, high = (_num(bar.get(k)) for k in ("close", "low", "high"))
    if not close:
        return empty

    reasons: list[str] = []
    window = bars[max(0, index - lookback):index]          # 不含当日，避免自己定义自己
    prior_high = max((_num(item.get("high")) for item in window), default=0.0)
    prior_low = min((_num(item.get("low")) for item in window if _num(item.get("low"))), default=0.0)
    if prior_high and prior_low and prior_high > prior_low:
        if close > prior_high:
            reasons.append(f"破 {lookback} 日高")
        elif close < prior_low:
            reasons.append(f"破 {lookback} 日低")
        else:
            position = (close - prior_low) / (prior_high - prior_low)
            if position <= edge_pct / 100:
                reasons.append(f"{lookback} 日区间下沿")
            elif position >= 1 - edge_pct / 100:
                reasons.append(f"{lookback} 日区间上沿")

    for span, name in ((20, "MA20"), (60, "MA60")):
        if index + 1 < span:
            continue
        average = _mean([_num(item.get("close")) for item in bars[index + 1 - span:index + 1]])
        prior_average = _mean([_num(item.get("close")) for item in bars[index - span:index]])
        if not average:
            continue
        if abs(close / average - 1) * 100 <= near_ma_pct:
            reasons.append(f"贴 {name}")
        elif prior_average:                               # 均线穿越：比"贴着"更硬的信号
            if _num(bars[index - 1].get("close")) < prior_average <= close:
                reasons.append(f"上穿 {name}")
            elif _num(bars[index - 1].get("close")) > prior_average >= close:
                reasons.append(f"下穿 {name}")

    if index + 1 < len(bars):                              # 分型要右边一根来确认
        left, right = _num(bars[index - 1].get("low")), _num(bars[index + 1].get("low"))
        # 只有"20 日新低附近的分型"才算关键位置。普通的局部小坑每三天就有一个，
        # 那种分型画上去只会把图铺满，等于没说。
        if low and left and right and low <= left and low <= right and prior_low \
                and low <= prior_low * (1 + THRESHOLDS["key_extreme_tol"] / 100):
            reasons.append("20 日新低分型")
        upper_left = _num(bars[index - 1].get("high"))
        upper_right = _num(bars[index + 1].get("high"))
        if high and upper_left and upper_right and high >= upper_left and high >= upper_right \
                and prior_high and high >= prior_high * (1 - THRESHOLDS["key_extreme_tol"] / 100):
            reasons.append("20 日新高分型")

    return {"key": bool(reasons), "reasons": reasons,
            "prior_high": prior_high or None, "prior_low": prior_low or None}


# ---------- 组合形态（两根以上） ----------

def _combos(bars: Sequence[dict], index: int, trend: str) -> tuple[list[str], list[str], dict]:
    """组合形态：星线、孕线、乌云/刺透、平头平底、三兵三鸦。

    单根 K 线只说明"当天多空谁赢了"；组合形态说明"两三天里力量怎么交接的"，
    这才是转折点上更早出现的东西。趋势前提按教科书写死：反转类形态必须出现在
    相应的趋势之后，否则"同形异位"的两件事会被混成一件事。
    """
    up: list[str] = []
    down: list[str] = []
    flags = {
        "morning_star": False, "evening_star": False,
        "three_white_soldiers": False, "three_black_crows": False,
        "harami_bull": False, "harami_bear": False,
        "dark_cloud_cover": False, "piercing": False,
        "tweezers_bottom": False, "tweezers_top": False,
    }
    if index < 2:
        return up, down, flags
    t = THRESHOLDS
    a, b, c = (geometry(bars[i]) for i in (index - 2, index - 1, index))
    if a["zero_range"] or b["zero_range"] or c["zero_range"]:
        return up, down, flags

    prev, cur = b, c                                  # 两根形态看的是"前一根 + 当根"
    window = bars[max(0, index - t["key_lookback"]):index]
    prior_high = max((_num(item.get("high")) for item in window), default=0.0)
    prior_low = min((_num(item.get("low")) for item in window if _num(item.get("low"))), default=0.0)
    extreme = t["key_extreme_tol"] / 100
    near = t["near_eq"]
    a_top, a_bottom = max(a["o"], a["c"]), min(a["o"], a["c"])
    b_top, b_bottom = max(b["o"], b["c"]), min(b["o"], b["c"])
    a_mid = (a["o"] + a["c"]) / 2.0
    p_top, p_bottom = max(prev["o"], prev["c"]), min(prev["o"], prev["c"])
    c_top, c_bottom = max(cur["o"], cur["c"]), min(cur["o"], cur["c"])
    prev_mid = (prev["o"] + prev["c"]) / 2.0

    # 早晨之星 / 黄昏之星：长实体 → 小实体（跳空或明显分离）→ 反向长实体收回一半
    if (a["bear"] and a["body_r"] >= t["long_body_r"]
            and b["body_r"] <= t["star_middle_r"] and b_top <= a_bottom * (1 + near)
            and c["bull"] and c["body_r"] >= t["long_body_r"] and c["c"] >= a_mid
            and trend == "down"):
        flags["morning_star"] = True
        up.append("早晨之星")
    if (a["bull"] and a["body_r"] >= t["long_body_r"]
            and b["body_r"] <= t["star_middle_r"] and b_bottom >= a_top * (1 - near)
            and c["bear"] and c["body_r"] >= t["long_body_r"] and c["c"] <= a_mid
            and trend == "up"):
        flags["evening_star"] = True
        down.append("黄昏之星")

    # 看涨/看跌孕线：子线实体完全缩在母线实体里（母子线，力量交接）
    if (prev["bear"] and prev["body_r"] >= t["long_body_r"] and cur["bull"]
            and c_top <= p_top and c_bottom >= p_bottom
            and cur["body"] <= prev["body"] * t["harami_body_r"] and trend != "up"):
        flags["harami_bull"] = True
        up.append("看涨孕线")
    if (prev["bull"] and prev["body_r"] >= t["long_body_r"] and cur["bear"]
            and c_top <= p_top and c_bottom >= p_bottom
            and cur["body"] <= prev["body"] * t["harami_body_r"] and trend != "down"):
        flags["harami_bear"] = True
        down.append("看跌孕线")

    # 乌云盖顶 / 刺透：高开（低开）穿过母线实体中点，但没完全吞掉——吞没已由单根那层管
    if (prev["bull"] and prev["body_r"] >= t["long_body_r"] and cur["bear"]
            and cur["o"] >= prev["c"] and cur["c"] <= prev_mid and cur["c"] > prev["o"]):
        flags["dark_cloud_cover"] = True
        down.append("乌云盖顶")
    if (prev["bear"] and prev["body_r"] >= t["long_body_r"] and cur["bull"]
            and cur["o"] <= prev["c"] and cur["c"] >= prev_mid and cur["c"] < prev["o"]):
        flags["piercing"] = True
        up.append("刺透形态")

    # 平底 / 平顶：两根的低点（高点）几乎相等，**而且这个"平"发生在 20 日极值上**。
    # 不加后半句的话，横盘里随便两根挨着的 K 线都"平"——实测每只票一年能触发 36 次，
    # 那种"平"说明不了任何事；加完只剩 7 次，才是教科书说的双底/双顶。
    if cur["bull"] and prev["l"] and abs(cur["l"] - prev["l"]) <= prev["l"] * near \
            and prior_low and prev["l"] <= prior_low * (1 + extreme):
        flags["tweezers_bottom"] = True
        up.append("平底")
    if cur["bear"] and prev["h"] and abs(cur["h"] - prev["h"]) <= prev["h"] * near \
            and prior_high and prev["h"] >= prior_high * (1 - extreme):
        flags["tweezers_top"] = True
        down.append("平顶")

    # 红三兵 / 三只乌鸦：三根同向、逐根抬升（下移）、每根都收在强侧
    soldiers = t["soldiers_body_r"]
    strong = t["soldiers_close_r"]
    if (a["bull"] and b["bull"] and c["bull"] and c["c"] > b["c"] > a["c"]
            and min(a["body_r"], b["body_r"], c["body_r"]) >= soldiers
            and min(a["close_pos"], b["close_pos"], c["close_pos"]) >= strong
            and trend != "up"):
        flags["three_white_soldiers"] = True
        up.append("红三兵")
    if (a["bear"] and b["bear"] and c["bear"] and c["c"] < b["c"] < a["c"]
            and min(a["body_r"], b["body_r"], c["body_r"]) >= soldiers
            and max(a["close_pos"], b["close_pos"], c["close_pos"]) <= 1 - strong
            and trend != "down"):
        flags["three_black_crows"] = True
        down.append("三只乌鸦")
    return up, down, flags


# ---------- 形态合成 ----------

_RESULT_KEYS = (
    "pattern", "reasons", "reversal_up", "reversal_down", "combo_up", "combo_down",
    "doji", "zero_range", "body_pct", "body_r", "upper_shadow", "lower_shadow",
    "close_pos", "volume_x", "key", "key_reasons", "trend",
    "long_bull", "long_bear", "hammer", "hanging_man", "shooting", "inverted_hammer",
    "bullish_engulf", "bearish_engulf", "gap_up", "gap_down", "breakout_10", "breakdown_10",
    "up_streak", "down_streak", "morning_star", "evening_star",
    "three_white_soldiers", "three_black_crows", "harami_bull", "harami_bear",
    "dark_cloud_cover", "piercing", "tweezers_bottom", "tweezers_top",
)

# 这些键的"没有结论"必须是 None，不能是 False：False 在 Python 里等于 0，
# 筛选条件里写 `candle.body_pct >= 0` 会被空结论悄悄判成"通过"。
_NONE_KEYS = ("body_pct", "body_r", "upper_shadow", "lower_shadow", "close_pos", "volume_x", "trend")


def blank() -> dict:
    """空结论——**所有键都在**。

    少一个键，调用方 `.get()` 拿到 None 就会静默失效。踩过：`obvious()` 判断的三个
    看跌标志位压根不在返回值里，查出来永远是 None，于是单个看跌形态一条都标不出来。
    """
    result = {key: False for key in _RESULT_KEYS}
    for key in _NONE_KEYS:
        result[key] = None
    result["reasons"] = []
    result["key_reasons"] = []
    result["pattern"] = ""
    return result


def analyze(bars: Sequence[dict], index: int, lookback: int | None = None) -> dict:
    """看第 index 根（含它自己）的形态。数据不足就返回空结论。"""
    lookback = int(lookback or THRESHOLDS["key_lookback"])
    result = blank()
    if index < 2 or index >= len(bars):
        return result

    bar = bars[index]
    g = geometry(bar)
    prev = bars[index - 1]
    prev_close, prev_open = _num(prev.get("close")), _num(prev.get("open"))
    prev_high, prev_low = _num(prev.get("high")), _num(prev.get("low"))
    if not g["c"] or not g["o"] or not g["h"] or not g["l"]:
        return result

    # 一字线（涨停跌停封死，或整天没有撮合）：没有振幅，不能拿比例去套形态。
    # 按比例算会得到 0/0 被标成"十字星"——含义正好相反：十字星是多空胶着，一字线是一边倒。
    if g["zero_range"]:
        result["pattern"] = "一字线（无振幅）"
        result["zero_range"] = True
        result["body_pct"] = round((g["c"] - prev_close) / prev_close * 100, 2) if prev_close else None
        result["trend"] = trend_before(bars, index)
        return result

    t = THRESHOLDS
    body_pct = (g["c"] - g["o"]) / prev_close * 100 if prev_close else None
    trend = trend_before(bars, index)
    doji = g["body_r"] <= t["doji_body_r"]

    # 量能：相对前 lookback 日均量（不含当日）
    window = [_num(item.get("volume")) for item in bars[max(0, index - lookback):index]]
    window = [value for value in window if value > 0]
    average = sum(window) / len(window) if window else 0
    volume_x = round(_num(bar.get("volume")) / average, 2) if average else None

    long_bull = bool(g["bull"] and body_pct is not None and body_pct >= t["long_body_pct"]
                     and g["close_pos"] >= t["close_high_r"])
    long_bear = bool(g["bear"] and body_pct is not None and body_pct <= -t["long_body_pct"]
                     and g["close_pos"] <= t["close_low_r"])
    # 锤子/上吊线、流星/倒锤星：形状相同，靠趋势背景分开——这就是"同形异位"
    shape_hammer = bool(g["lower_r"] >= t["long_shadow_r"] and g["upper_r"] <= t["tiny_shadow_r"]
                        and g["body_r"] <= t["small_body_r"]
                        and g["lower"] >= t["shadow_mult"] * g["body"])
    shape_star = bool(g["upper_r"] >= t["long_shadow_r"] and g["lower_r"] <= t["tiny_shadow_r"]
                      and g["body_r"] <= t["small_body_r"]
                      and g["upper"] >= t["shadow_mult"] * g["body"])
    hammer = shape_hammer and trend == "down"
    hanging_man = shape_hammer and trend == "up"
    shooting = shape_star and trend == "up"
    inverted_hammer = shape_star and trend == "down"

    bullish_engulf = bool(g["bull"] and prev_close < prev_open
                          and g["c"] >= prev_open and g["o"] <= prev_close)
    bearish_engulf = bool(g["bear"] and prev_close > prev_open
                          and g["c"] <= prev_open and g["o"] >= prev_close)
    gap_up = bool(prev_high and g["l"] > prev_high)
    gap_down = bool(prev_low and g["h"] < prev_low)

    recent = bars[max(0, index - 9):index]                 # 前 10 根，不含当日
    prior_10_high = max((_num(item.get("high")) for item in recent), default=0)
    prior_10_low = min((_num(item.get("low")) for item in recent if _num(item.get("low"))), default=0)
    breakout = bool(prior_10_high and g["c"] > prior_10_high)
    breakdown = bool(prior_10_low and g["c"] < prior_10_low)

    up_streak = down_streak = 0
    for cursor in range(index, max(-1, index - 6), -1):
        if _num(bars[cursor].get("close")) > _num(bars[cursor].get("open")):
            if down_streak:
                break
            up_streak += 1
        else:
            if up_streak:
                break
            down_streak += 1

    combo_up, combo_down, combo_flags = _combos(bars, index, trend)

    reasons_up: list[str] = []
    if long_bull:
        reasons_up.append("放量长阳" if (volume_x or 0) >= t["shadow_volume_x"] else "长阳")
    if bullish_engulf:
        reasons_up.append("阳包阴")
    if hammer:
        reasons_up.append("锤子（下跌后）")
    if inverted_hammer:
        reasons_up.append("倒锤星（下跌后）")
    if gap_up:
        reasons_up.append("向上跳空")
    if breakout:
        reasons_up.append("站上前 10 日高点")
    reasons_up.extend(combo_up)

    reasons_down: list[str] = []
    if long_bear:
        reasons_down.append("放量长阴" if (volume_x or 0) >= t["shadow_volume_x"] else "长阴")
    if bearish_engulf:
        reasons_down.append("阴包阳")
    if hanging_man:
        reasons_down.append("上吊线（上涨后）")
    if shooting:
        reasons_down.append("流星（上涨后）")
    if gap_down:
        reasons_down.append("向下跳空")
    if breakdown:
        reasons_down.append("跌破前 10 日低点")
    reasons_down.extend(combo_down)

    spot = key_position(bars, index, lookback)
    result.update(
        {
            "pattern": "、".join(reasons_up or reasons_down) or ("十字星" if doji else ""),
            "reasons": reasons_up or reasons_down,
            "body_pct": round(body_pct, 2) if body_pct is not None else None,
            "body_r": round(g["body_r"], 4),
            "upper_shadow": round(g["upper_r"], 4),
            "lower_shadow": round(g["lower_r"], 4),
            "close_pos": round(g["close_pos"], 4),
            "volume_x": volume_x,
            "trend": trend,
            "doji": bool(doji),
            "long_bull": long_bull,
            "long_bear": long_bear,
            "hammer": hammer,
            "hanging_man": hanging_man,
            "shooting": shooting,
            "inverted_hammer": inverted_hammer,
            "bullish_engulf": bullish_engulf,
            "bearish_engulf": bearish_engulf,
            "gap_up": gap_up,
            "gap_down": gap_down,
            "breakout_10": breakout,
            "breakdown_10": breakdown,
            "up_streak": up_streak,
            "down_streak": down_streak,
            "combo_up": bool(combo_up),
            "combo_down": bool(combo_down),
            "key": spot["key"],
            "key_reasons": spot["reasons"],
            # 反转确认：至少两个独立信号，**或者**一个组合形态。
            # 组合形态本身已经把两三根 K 线的关系算进去了，不需要再叠一条。
            "reversal_up": len(reasons_up) >= 2 or bool(combo_up),
            "reversal_down": len(reasons_down) >= 2 or bool(combo_down),
        }
    )
    result.update(combo_flags)
    return result


def obvious(result: dict) -> bool:
    """算不算"明显形态"——形态本身要够硬，才值得画在图上、才值得提醒。

    `analyze` 对每根 K 线都会给出一个描述（十字星、长阳、局部小坑的分型……），
    但"今天收了根阳线"不是形态。这里要求至少一条硬证据：
      组合形态 / 反转确认（≥2 个信号）/ 跳空 / 放量长实体 / 放量吞没 / 放量长影线 / 放量突破。
    """
    if not result or not result.get("pattern"):
        return False
    if result.get("combo_up") or result.get("combo_down"):
        return True
    if result.get("reversal_up") or result.get("reversal_down"):
        return True
    t = THRESHOLDS
    volume_x = result.get("volume_x") or 0.0
    if (result.get("long_bull") or result.get("long_bear")) and volume_x >= t["volume_confirm_x"]:
        return True
    if (result.get("bullish_engulf") or result.get("bearish_engulf")) and volume_x >= t["engulf_volume_x"]:
        return True
    if result.get("gap_up") or result.get("gap_down"):
        return True
    if any(result.get(key) for key in ("hammer", "hanging_man", "shooting", "inverted_hammer")) \
            and volume_x >= t["shadow_volume_x"]:
        return True
    if (result.get("breakout_10") or result.get("breakdown_10")) and volume_x >= t["volume_confirm_x"]:
        return True
    return False


def marks(bars: Sequence[dict], lookback: int | None = None, limit: int | None = None) -> list[dict]:
    """挑出"值得画在图上"的形态：**落在关键位置**上的明显形态。

    每日都画形态等于没有形态。所以这里返回的是全集的一个子集，
    前端只负责画，口径在这一层定死（页面不自己发明筛选逻辑）。
    """
    output: list[dict] = []
    last_index: dict[str, int] = {}
    for index in range(2, len(bars)):
        candle = analyze(bars, index, lookback)
        if not obvious(candle) or not candle.get("key"):
            continue
        # 同一段行情里连着几根都是"阴包阳"，只画第一根：形态的意义在于"它出现在哪里"，
        # 连着三根同类形态不是三个信号，是一个信号被抄了三遍。
        direction = "up" if (candle["reversal_up"] or candle["combo_up"]) else (
            "down" if (candle["reversal_down"] or candle["combo_down"]) else "flat")
        previous = last_index.get(direction)
        if previous is not None and index - previous <= 2:
            last_index[direction] = index
            continue
        last_index[direction] = index
        output.append(
            {
                "trade_date": bars[index].get("trade_date"),
                "pattern": candle["pattern"],
                "up": bool(candle["reversal_up"]),
                "down": bool(candle["reversal_down"]),
                "combo": bool(candle["combo_up"] or candle["combo_down"]),
                "reasons": candle["key_reasons"],
                "volume_x": candle.get("volume_x"),
                "body_pct": candle.get("body_pct"),
            }
        )
    if limit:
        output = output[-limit:]
    return output


def describe(result: dict) -> str:
    if not result or not result.get("pattern"):
        return "无显著形态"
    parts = [result["pattern"]]
    if result.get("volume_x") and result["volume_x"] >= THRESHOLDS["shadow_volume_x"]:
        parts.append(f"量 {result['volume_x']:.2f} 倍")
    if result.get("key"):
        parts.append("关键位置：" + "、".join(result.get("key_reasons") or []))
    if result.get("reversal_up"):
        parts.append("反转向上确认")
    elif result.get("reversal_down"):
        parts.append("反转向下确认")
    return "，".join(parts)
