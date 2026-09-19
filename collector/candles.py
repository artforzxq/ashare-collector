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
    # 多根形态（三根以上）
    "swing_span": 2,            # 摆动点：左右各几根才算一个拐点
    "methods_middle_min": 2,    # 三法：母线之后、确认线之前允许几根整理
    "methods_middle_max": 4,
    "methods_long_r": 0.50,     # 三法：母线/末根的实体占比下限
    "methods_small_r": 0.45,    # 三法：整理那几根的实体占比上限
    "methods_tol": 0.15,        # 三法：整理可以超出母线全幅多少（占母线全幅）
    "peak_lookback": 60,        # 三重顶/头肩/圆弧：回看窗口上限
    "peak_min_bars": 24,        # 窗口下限（太短形不成图形）
    "peak_swing_span": 3,       # 图形形态用的摆动点更严（左右各 3 根）
    "peak_level_tol": 0.05,     # 三个峰/谷"大致同高"的相对容差
    "peak_drop": 0.03,          # 峰间回撤（谷间反弹）的最小幅度
    "hs_shoulder_tol": 0.06,    # 头肩：两肩彼此近似的容差
    "hs_head_margin": 0.01,     # 头肩：头要明显高于（低于）两肩
    "rounding_buckets": 3,      # 圆弧：把窗口分几段比高低
    "rounding_arc": 0.03,       # 圆弧：两端均值与中段的相对落差，也当确认门槛
    "rounding_basin_band": 0.25,  # 碗底带宽：收盘落在窗口最低 25% 区间内算"贴底"
    "rounding_basin_share": 0.20, # 贴底的根数占比下限（V 形只有一两个点贴底，碗形一大片）
    "island_max_bars": 10,      # 岛形：两个缺口之间最多隔几根
    "island_level_tol": 0.02,   # 岛形：确认时至少要回到跳空前的水平（留一点容差）
}


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _mean(values: Sequence[float]) -> float | None:
    clean = [value for value in values if value]
    return sum(clean) / len(clean) if clean else None


def flow(bar: dict) -> float:
    """这一根 K 线的"量"：**优先成交额**，拿不到才退回成交量。

    为什么这么定：成交额（元）才是"真金白银"，而且不受股价水平影响——同一只票
    涨了 10% 的时候，成交量没变，成交额也已经放大了 10%。指数拿不到成交额
    （成交量 × 点位不是金额），所以退回成交量；同一个标的内部口径统一，
    量比这类相对指标仍然可比。

    这条规则以前只写在特征层（`vol_ratio_20`、突破确认都用它），形态层却只取成交量，
    于是同一天会同时出现"特征层说放量、形态层说没放量"——两道门槛各算各的。
    现在只有这一处定义，两层都从这里取。
    """
    try:
        amount = float(bar.get("amount") or 0.0)
    except (TypeError, ValueError):
        amount = 0.0
    return amount if amount > 0 else _num(bar.get("volume"))


# ---------- 摆动点：图形形态的地基 ----------

def swings(values: Sequence[float], span: int | None = None,
           kind: str = "low") -> tuple[list[int], list[float]]:
    """摆动点（分型）：左右各 span 根都不更低（更高）才算一个拐点。

    返回 (下标, 价格) 两条平行列表，下标天然递增，调用方用二分查"到某天为止的最后一个"。
    右边那 span 根是**确认成本**：最近几根永远还不是摆动点——这是不用未来函数的代价。
    """
    span = int(THRESHOLDS["swing_span"] if span is None else span)
    days: list[int] = []
    prices: list[float] = []
    for index in range(span, len(values) - span):
        window = values[index - span:index + span + 1]
        extreme = min(window) if kind == "low" else max(window)
        if values[index] == extreme:
            # 平台上相邻的两根会各自满足条件（真实数据里平的峰谷很常见：连着两天同高点）。
            # 它们其实是**同一个**拐点，不去重的话"找最近三个峰"会变成"同一个峰算两次"，
            # 三重顶、头肩都会因此误判。只合并相邻的：隔得远而价格相同的（真·双顶）要留着。
            if days and prices and prices[-1] == float(values[index]) and index - days[-1] <= span:
                continue
            days.append(index)
            prices.append(float(values[index]))
    return days, prices


def swing_context(bars: Sequence[dict], span: int | None = None) -> dict:
    """把整条序列的摆动点预计算好，供同一序列上的多次 analyze 复用。

    为什么要这个：回放一次要跑上百万次 analyze，每次都从头扫窗口的话，
    光摆动点就是几亿次比较。算一次、传下去，纯函数还是纯函数。
    """
    span = int(THRESHOLDS["swing_span"] if span is None else span)
    highs = [_num(bar.get("high")) for bar in bars]
    lows = [_num(bar.get("low")) for bar in bars]
    high_days, high_prices = swings(highs, span, "high")
    low_days, low_prices = swings(lows, span, "low")
    return {"span": span, "high_days": high_days, "high_prices": high_prices,
            "low_days": low_days, "low_prices": low_prices}


def context(bars: Sequence[dict]) -> dict:
    """一条序列上要用的全部预计算，供同一序列上的多次 analyze 复用。

    两套摆动点：普通形态用左右各 2 根（`swing`），三重顶/头肩这类图形形态用左右各 3 根
    （`graphic`）——图形形态本来就该只认显著的峰谷，用松口径会把噪音当山峰。
    """
    return {"swing": swing_context(bars),
            "graphic": swing_context(bars, int(THRESHOLDS["peak_swing_span"]))}


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

def _methods(bars: Sequence[dict], index: int) -> tuple[list[str], list[str], dict]:
    """三法（上升三法 / 下降三法）：长实体 → 几根小实体整理（不破母线）→ 同向长实体确认。

    这是教科书里少见的**延续**形态：它说的是"歇了一口气还会接着走"，
    和前面那些反转形态方向语义相反，所以它不参与"反转确认"。
    整理段的根数允许 2~4 根（书上是 3 根），取最长的那组——形态越长，越不像巧合。
    """
    up: list[str] = []
    down: list[str] = []
    flags = {"rising_three_methods": False, "falling_three_methods": False}
    t = THRESHOLDS
    for middle in range(int(t["methods_middle_max"]), int(t["methods_middle_min"]) - 1, -1):
        first = index - middle - 1
        if first < 1:
            continue
        a, c = geometry(bars[first]), geometry(bars[index])
        if a["zero_range"] or c["zero_range"]:
            continue
        mids = [geometry(bars[first + offset]) for offset in range(1, middle + 1)]
        if any(m["zero_range"] for m in mids):
            continue
        a_top, a_bottom = max(a["o"], a["c"]), min(a["o"], a["c"])
        tol = t["methods_tol"] * a["rng"]
        inside = all(
            max(m["o"], m["c"]) <= a_top + tol and min(m["o"], m["c"]) >= a_bottom - tol
            and m["body_r"] <= t["methods_small_r"]
            for m in mids
        )
        if not inside:
            continue
        if (a["bull"] and a["body_r"] >= t["methods_long_r"] and c["bull"]
                and c["body_r"] >= t["methods_long_r"] and c["c"] > a["c"]):
            flags["rising_three_methods"] = True
            up.append("上升三法")
            return up, down, flags
        if (a["bear"] and a["body_r"] >= t["methods_long_r"] and c["bear"]
                and c["body_r"] >= t["methods_long_r"] and c["c"] < a["c"]):
            flags["falling_three_methods"] = True
            down.append("下降三法")
            return up, down, flags
    return up, down, flags


def _swing_points(context: dict, bars: Sequence[dict], index: int, limit: int, span: int,
                  kind: str) -> list[tuple[int, float]]:
    """窗口内已经确认的摆动点（从近到远），最多 limit 个。"""
    days = context[kind + "_days"]
    prices = context[kind + "_prices"]
    start = max(0, index - int(THRESHOLDS["peak_lookback"]))
    found: list[tuple[int, float]] = []
    for position in range(len(days) - 1, -1, -1):
        day = days[position]
        if day > index - span:            # 还没被右边确认的，不算
            continue
        if day < start:
            break
        found.append((day, prices[position]))
        if len(found) >= limit:
            break
    return list(reversed(found))


def _graphic(bars: Sequence[dict], index: int, context: dict) -> tuple[list[str], list[str], dict]:
    """图形形态：三重顶/底、头肩顶/底、圆弧顶/底。都要窗口够长 + 摆动点确认。

    和单根/两根形态的区别在于"看的是形状"：它们用摆动点找峰谷，而不是逐根比实体，
    所以必须**右边等 span 根**才算数——这也是它们天然滞后的原因（认出来的时候，
    行情已经走了一截）。这一点写在形态名里不算，但提醒文案会带上确认的那一根。
    """
    up: list[str] = []
    down: list[str] = []
    flags = {"triple_top": False, "triple_bottom": False,
             "head_shoulders_top": False, "head_shoulders_bottom": False,
             "rounding_top": False, "rounding_bottom": False}
    t = THRESHOLDS
    span = int(t["peak_swing_span"])
    if index < int(t["peak_min_bars"]):
        return up, down, flags
    close = _num(bars[index].get("close"))
    prev_close = _num(bars[index - 1].get("close"))
    if not close or not prev_close:
        return up, down, flags

    highs = _swing_points(context, bars, index, 3, span, "high")
    lows = _swing_points(context, bars, index, 3, span, "low")

    def valleys_between(left: int, right: int) -> float | None:
        window = [_num(bar.get("low")) for bar in bars[left + 1:right] if _num(bar.get("low"))]
        return min(window) if window else None

    def peaks_between(left: int, right: int) -> float | None:
        window = [_num(bar.get("high")) for bar in bars[left + 1:right] if _num(bar.get("high"))]
        return max(window) if window else None

    # ---- 三重顶 / 头肩顶：三个峰，看峰的相对高度和之间的谷 ----
    if len(highs) == 3:
        (d1, p1), (d2, p2), (d3, p3) = highs
        levels = sorted([p1, p2, p3])
        spread = (levels[-1] - levels[0]) / max(1e-9, levels[-1])
        valley1, valley2 = valleys_between(d1, d2), valleys_between(d2, d3)
        neckline = min(v for v in (valley1, valley2) if v) if (valley1 and valley2) else None
        retraced = bool(valley1 and valley2
                        and valley1 <= levels[-1] * (1 - t["peak_drop"])
                        and valley2 <= levels[-1] * (1 - t["peak_drop"]))
        # 确认必须是**刚发生的那一次跌破**（昨天还在颈线上方），不能是"现在处在颈线下方"——
        # 后者意味着跌下去之后的每一天都算命中，实测单这一条就让三重顶多出十几倍。
        if neckline and retraced and prev_close >= neckline > close:
            # 头肩顶：中间那个峰明显更高、两肩大致同高
            shoulders = [p1, p3]
            if (p2 >= max(shoulders) * (1 + t["hs_head_margin"])
                    and abs(p1 - p3) / max(1e-9, max(p1, p3)) <= t["hs_shoulder_tol"]):
                flags["head_shoulders_top"] = True
                down.append("头肩顶")
            elif spread <= t["peak_level_tol"]:
                flags["triple_top"] = True
                down.append("三重顶")

    # ---- 三重底 / 头肩底：三个谷，镜像 ----
    if len(lows) == 3:
        (d1, p1), (d2, p2), (d3, p3) = lows
        levels = sorted([p1, p2, p3])
        spread = (levels[-1] - levels[0]) / max(1e-9, levels[0])
        peak1, peak2 = peaks_between(d1, d2), peaks_between(d2, d3)
        neckline = max(v for v in (peak1, peak2) if v) if (peak1 and peak2) else None
        rebounded = bool(peak1 and peak2
                         and peak1 >= levels[0] * (1 + t["peak_drop"])
                         and peak2 >= levels[0] * (1 + t["peak_drop"]))
        if neckline and rebounded and prev_close <= neckline < close:
            shoulders = [p1, p3]
            if (p2 <= min(shoulders) * (1 - t["hs_head_margin"])
                    and abs(p1 - p3) / max(1e-9, max(p1, p3)) <= t["hs_shoulder_tol"]):
                flags["head_shoulders_bottom"] = True
                up.append("头肩底")
            elif spread <= t["peak_level_tol"]:
                flags["triple_bottom"] = True
                up.append("三重底")

    # ---- 圆弧顶 / 圆弧底：把窗口分三段，看中段是不是凹/凸，而且全程以小实体为主 ----
    lookback = min(int(t["peak_lookback"]), index)
    if lookback >= int(t["peak_min_bars"]):
        window = bars[index - lookback:index + 1]
        closes = [_num(bar.get("close")) for bar in window]
        buckets = int(t["rounding_buckets"])
        size = len(window) // buckets
        if size >= 3:
            low, high = min(closes), max(closes)
            low_at, high_at = closes.index(low), closes.index(high)
            middle_from, middle_to = size, len(window) - size
            left_mean = _mean(closes[:size])
            # 碗底够宽才是圆弧：V 形只有一两个点贴着底，碗形会有一大片
            band_width = (high - low) * t["rounding_basin_band"]
            basin_share = sum(1 for value in closes if value <= low + band_width) / len(closes)
            cap_share = sum(1 for value in closes if value >= high - band_width) / len(closes)
            arc = t["rounding_arc"]
            share = t["rounding_basin_share"]
            if high > low and left_mean:
                # 确认门槛：刚从碗底抬起来（涨过碗底的 arc），或刚从拱顶滑下来。
                # 用"刚发生的那一次穿越"，不是"现在处在某个位置"——后者每天都会重报。
                rim = low * (1 + arc)
                eave = high * (1 - arc)
                # "极值落在窗口中部"才是圆弧：单调下跌的极值在最右边，一路上涨的在最左边
                if (basin_share >= share and middle_from <= low_at < middle_to and left_mean >= rim
                        and prev_close <= rim < close):
                    flags["rounding_bottom"] = True
                    up.append("圆弧底")
                elif (cap_share >= share and middle_from <= high_at < middle_to and left_mean <= eave
                      and prev_close >= eave > close):
                    flags["rounding_top"] = True
                    down.append("圆弧顶")
    return up, down, flags


def _island(bars: Sequence[dict], index: int) -> tuple[list[str], list[str], dict]:
    """岛形反转：先向下跳空、隔几根再向上跳空（两个缺口价位相近），中间那段成了孤岛。

    A 股跳空本来就少，这个形态很稀有——稀有是好事：它出现的时候通常是有事发生了。
    """
    up: list[str] = []
    down: list[str] = []
    flags = {"island_bottom": False, "island_top": False}
    t = THRESHOLDS
    span = int(t["island_max_bars"])
    start = max(1, index - span)
    close = _num(bars[index].get("close"))
    if not close:
        return up, down, flags
    for position in range(max(1, index - span), index + 1):
        bar = bars[position]
        prev = bars[position - 1]
        gap_down = _num(bar.get("high")) < _num(prev.get("low"))
        gap_up = _num(bar.get("low")) > _num(prev.get("high"))
        if not (gap_down or gap_up):
            continue
        # 第一个跳空留下的那段"真空价格带"：反向跳空必须回落到这条带里，
        # 这才是教科书说的"两个缺口大致在同一价位"——不是拿两个收盘价比大小。
        band = ((_num(bar.get("high")), _num(prev.get("low"))) if gap_down
                else (_num(prev.get("high")), _num(bar.get("low"))))
        tol = t["island_level_tol"]
        for other in range(position + 1, index + 1):
            if other - position > span:
                break
            if other < index - 1:
                continue          # 第二个缺口必须是"刚出现"的，否则这段行情会被反复报好几天
            here, before = bars[other], bars[other - 1]
            if gap_down and _num(here.get("low")) > _num(before.get("high")) \
                    and band[0] * (1 - tol) <= _num(here.get("low")) <= band[1] * (1 + tol):
                flags["island_bottom"] = True
                up.append("岛形反转（底）")
                return up, down, flags
            if gap_up and _num(here.get("high")) < _num(before.get("low")) \
                    and band[0] * (1 - tol) <= _num(here.get("high")) <= band[1] * (1 + tol):
                flags["island_top"] = True
                down.append("岛形反转（顶）")
                return up, down, flags
        if position - start > span:
            break
    return up, down, flags


def _multi(bars: Sequence[dict], index: int, ctx: dict | None = None) -> tuple[list[str], list[str], dict]:
    """三根以上的形态：三法（延续）+ 三重顶底 / 头肩 / 圆弧 / 岛形（反转）。

    ctx 是**图形形态用的严格摆动点**（`swing_context(bars, peak_swing_span)`）。
    """
    up: list[str] = []
    down: list[str] = []
    flags = {"rising_three_methods": False, "falling_three_methods": False,
             "triple_top": False, "triple_bottom": False,
             "head_shoulders_top": False, "head_shoulders_bottom": False,
             "rounding_top": False, "rounding_bottom": False,
             "island_top": False, "island_bottom": False}
    catalog = (
        (_methods, (bars, index)),
        (_graphic, (bars, index, ctx or swing_context(bars, int(THRESHOLDS["peak_swing_span"])))),
        (_island, (bars, index)),
    )
    for function, args in catalog:
        part_up, part_down, part_flags = function(*args)
        up.extend(part_up)
        down.extend(part_down)
        flags.update({key: value for key, value in part_flags.items() if value})
    return up, down, flags


_RESULT_KEYS = (
    "pattern", "reasons", "reversal_up", "reversal_down", "combo_up", "combo_down",
    "doji", "zero_range", "body_pct", "body_r", "upper_shadow", "lower_shadow",
    "close_pos", "volume_x", "key", "key_reasons", "trend",
    "long_bull", "long_bear", "hammer", "hanging_man", "shooting", "inverted_hammer",
    "bullish_engulf", "bearish_engulf", "gap_up", "gap_down", "breakout_10", "breakdown_10",
    "up_streak", "down_streak", "morning_star", "evening_star",
    "three_white_soldiers", "three_black_crows", "harami_bull", "harami_bear",
    "dark_cloud_cover", "piercing", "tweezers_bottom", "tweezers_top",
    "rising_three_methods", "falling_three_methods",
    "triple_top", "triple_bottom", "head_shoulders_top", "head_shoulders_bottom",
    "rounding_top", "rounding_bottom", "island_top", "island_bottom",
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


def analyze(bars: Sequence[dict], index: int, lookback: int | None = None,
            ctx: dict | None = None) -> dict:
    """看第 index 根（含它自己）的形态。数据不足就返回空结论。

    ctx 是 `swing_context(bars)` 的结果：同一条序列上要反复 analyze 时（回放、全市场扫描、
    画图）传进来，省掉每次重扫摆动点。不传就现算，纯函数的行为不变。
    """
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

    # 量能：相对前 lookback 日均量（不含当日）。量口径与特征层同一处定义（见 flow()）。
    window = [flow(item) for item in bars[max(0, index - lookback):index]]
    window = [value for value in window if value > 0]
    average = sum(window) / len(window) if window else 0
    volume_x = round(flow(bar) / average, 2) if average else None

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
    multi_up, multi_down, multi_flags = _multi(bars, index, ctx)

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
    reasons_up.extend(multi_up)

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
    reasons_down.extend(multi_down)

    # 反转类图形形态（三重顶底 / 头肩 / 圆弧 / 岛形）本身就是确认；
    # 三法是**延续**形态，方向语义相反，不算反转确认。
    graphic_up = any(multi_flags[key] for key in
                     ("triple_bottom", "head_shoulders_bottom", "rounding_bottom", "island_bottom"))
    graphic_down = any(multi_flags[key] for key in
                       ("triple_top", "head_shoulders_top", "rounding_top", "island_top"))

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
            "combo_up": bool(combo_up or graphic_up),
            "combo_down": bool(combo_down or graphic_down),
            "key": spot["key"],
            "key_reasons": spot["reasons"],
            # 反转确认：至少两个独立信号，**或者**一个组合形态。
            # 组合形态本身已经把两三根 K 线的关系算进去了，不需要再叠一条。
            "reversal_up": len(reasons_up) >= 2 or bool(combo_up),
            "reversal_down": len(reasons_down) >= 2 or bool(combo_down),
        }
    )
    result.update(combo_flags)
    result.update({key: value for key, value in multi_flags.items() if value})
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
    if result.get("rising_three_methods") or result.get("falling_three_methods"):
        return True      # 三法是延续形态，方向语义和"反转"不同，但形态本身够硬
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
    ctx = context(bars)["graphic"]
    for index in range(2, len(bars)):
        candle = analyze(bars, index, lookback, ctx)
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
