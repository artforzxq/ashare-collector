"""K 线形态：把"这根 K 线长什么样"变成能计算的字段。

为什么需要这一层：均线、ATR、量能都是**平滑后**的指标，天生滞后。
转折点上真正先动的是几根 K 线的组合形态——放量长阳、吞没、锤子、缺口、站上平台。
状态机要等趋势分从 45 爬到 70 才认账，那时候第一波已经走完了。

这一层只描述形态、给出"反转确认"与否，不做预测，也不单独下结论。
和项目其它部分一样：机械地描述事实。
"""

from __future__ import annotations

from typing import Sequence


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def analyze(bars: Sequence[dict], index: int, lookback: int = 20) -> dict:
    """看第 index 根（含它自己）的形态。数据不足就返回空结论。"""
    empty = {"pattern": "", "reversal_up": False, "reversal_down": False, "reasons": []}
    if index < 2 or index >= len(bars):
        return empty

    bar = bars[index]
    prev = bars[index - 1]
    close, open_, high, low = (_num(bar.get(k)) for k in ("close", "open", "high", "low"))
    prev_close, prev_open = _num(prev.get("close")), _num(prev.get("open"))
    prev_high, prev_low = _num(prev.get("high")), _num(prev.get("low"))
    if not close or not open_ or not high or not low or not prev_close:
        return empty

    span = max(high - low, 1e-9)
    body = close - open_
    body_pct = body / prev_close * 100
    upper = (high - max(open_, close)) / span
    lower = (min(open_, close) - low) / span
    bull = close > open_
    doji = abs(body) / span < 0.1

    # 量能：相对前 lookback 日均量（不含当日）
    window = [_num(item.get("volume")) for item in bars[max(0, index - lookback):index]]
    window = [value for value in window if value > 0]
    average = sum(window) / len(window) if window else 0
    volume_x = round(_num(bar.get("volume")) / average, 2) if average else None

    long_bull = bull and body_pct >= 2.0 and close >= low + span * 0.66
    long_bear = (not bull) and body_pct <= -2.0 and close <= low + span * 0.34
    hammer = lower >= 0.6 and upper <= 0.25 and close >= low + span * 0.5
    shooting = upper >= 0.6 and lower <= 0.25 and close <= low + span * 0.5
    bullish_engulf = bull and prev_close < prev_open and close >= prev_open and open_ <= prev_close
    bearish_engulf = (not bull) and prev_close > prev_open and close <= prev_open and open_ >= prev_close
    gap_up = low > prev_high
    gap_down = high < prev_low

    recent = bars[max(0, index - 9):index]                    # 前 10 根，不含当日
    prior_high = max((_num(item.get("high")) for item in recent), default=0)
    prior_low = min((_num(item.get("low")) for item in recent if _num(item.get("low"))), default=0)
    breakout = bool(prior_high and close > prior_high)
    breakdown = bool(prior_low and close < prior_low)

    swing_low = False                                          # 底分型：左右各一根更高的低点
    if index >= 1 and index + 1 < len(bars):
        left, right = _num(bars[index - 1].get("low")), _num(bars[index + 1].get("low"))
        swing_low = low <= left and low <= right

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

    reasons_up: list[str] = []
    if long_bull:
        reasons_up.append(f"放量长阳" if (volume_x or 0) >= 1.3 else "长阳")
    if bullish_engulf:
        reasons_up.append("阳包阴")
    if hammer:
        reasons_up.append("长下影（锤子）")
    if gap_up:
        reasons_up.append("向上跳空")
    if breakout:
        reasons_up.append("站上前 10 日高点")
    if swing_low:
        reasons_up.append("底分型")

    reasons_down: list[str] = []
    if long_bear:
        reasons_down.append("放量长阴" if (volume_x or 0) >= 1.3 else "长阴")
    if bearish_engulf:
        reasons_down.append("阴包阳")
    if shooting:
        reasons_down.append("长上影（射击之星）")
    if gap_down:
        reasons_down.append("向下跳空")
    if breakdown:
        reasons_down.append("跌破前 10 日低点")

    pattern = "、".join(reasons_up or reasons_down) or ("十字星" if doji else "")
    return {
        "pattern": pattern,
        "body_pct": round(body_pct, 2),
        "upper_shadow": round(upper, 2),
        "lower_shadow": round(lower, 2),
        "volume_x": volume_x,
        "doji": doji,
        "long_bull": long_bull,
        "long_bear": long_bear,
        "hammer": hammer,
        "bullish_engulf": bullish_engulf,
        "gap_up": gap_up,
        "breakout_10": breakout,
        "breakdown_10": breakdown,
        "up_streak": up_streak,
        "down_streak": down_streak,
        # 反转确认：至少两个独立信号同时出现才算，避免单根 K 线就下结论
        "reversal_up": len(reasons_up) >= 2,
        "reversal_down": len(reasons_down) >= 2,
        "reasons": reasons_up or reasons_down,
    }


def describe(result: dict) -> str:
    if not result or not result.get("pattern"):
        return "无显著形态"
    parts = [result["pattern"]]
    if result.get("volume_x") and result["volume_x"] >= 1.3:
        parts.append(f"量 {result['volume_x']:.2f} 倍")
    if result.get("reversal_up"):
        parts.append("反转向上确认")
    elif result.get("reversal_down"):
        parts.append("反转向下确认")
    return "，".join(parts)


# ---------- 关键位置：形态只有长在这里才值得看 ----------

# 形态本身没有方向，位置才有。同样一根锤子，出现在 20 日新低附近的支撑上，
# 和出现在一段没意义的横盘中间，含义完全不同。所以页面上只画"关键位置的形态"，
# 其余的日子不画——每天都画等于没有重点，看的人会自动忽略全部标记。
#
# 判定只用当日及之前的数据（前 20 日高/低、当日均线），不用未来函数；
# 底分型/顶分型天然要右边一根才能确认，所以最近一根永远不算分型（这是对的）。
KEY_NEAR_MA_PCT = 0.8      # 收盘离 MA20/MA60 多近算"贴在均线上"
KEY_EDGE_PCT = 12.0        # 收盘落进 20 日区间上下 12% 算"在边缘"


def _mean(values: Sequence[float]) -> float | None:
    clean = [value for value in values if value]
    return sum(clean) / len(clean) if clean else None


def key_position(bars: Sequence[dict], index: int, lookback: int = 20,
                 near_ma_pct: float = KEY_NEAR_MA_PCT,
                 edge_pct: float = KEY_EDGE_PCT) -> dict:
    """这根 K 线是不是落在关键位置（区间边缘 / 均线附近 / 分型点）。"""
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
                and low <= prior_low * 1.01:
            reasons.append("20 日新低分型")
        upper_left, upper_right = _num(bars[index - 1].get("high")), _num(bars[index + 1].get("high"))
        if high and upper_left and upper_right and high >= upper_left and high >= upper_right \
                and prior_high and high >= prior_high * 0.99:
            reasons.append("20 日新高分型")

    return {"key": bool(reasons), "reasons": reasons,
            "prior_high": prior_high or None, "prior_low": prior_low or None}


def marks(bars: Sequence[dict], lookback: int = 20, limit: int | None = None) -> list[dict]:
    """挑出"值得画在图上"的形态：**落在关键位置**的那些。

    每日都画形态等于没有形态。所以这里返回的是全集的一个子集，
    前端只负责画，口径在这一层定死（页面不自己发明筛选逻辑）。
    """
    output: list[dict] = []
    last_date_index: dict[str, int] = {}
    for index in range(2, len(bars)):
        candle = analyze(bars, index, lookback)
        if not candle.get("pattern") or not obvious(candle):
            continue
        spot = key_position(bars, index, lookback)
        if not spot["key"]:
            continue
        # 同一段行情里连着几根都是"阴包阳"，只画第一根：形态的意义在于"它出现在哪里"，
        # 连着三根同类形态不是三个信号，是一个信号被抄了三遍。
        direction = "up" if candle["reversal_up"] else ("down" if candle["reversal_down"] else "flat")
        previous = last_date_index.get(direction)
        if previous is not None and index - previous <= 2:
            last_date_index[direction] = index
            continue
        last_date_index[direction] = index
        output.append(
            {
                "trade_date": bars[index].get("trade_date"),
                "pattern": candle["pattern"],
                "up": bool(candle["reversal_up"]),
                "down": bool(candle["reversal_down"]),
                "reasons": spot["reasons"],
                "vol_x": candle.get("volume_x"),
                "body_pct": candle.get("body_pct"),
            }
        )
    if limit:
        output = output[-limit:]
    return output


def obvious(result: dict) -> bool:
    """算不算"明显形态"——形态本身要够硬，才值得画。

    为什么还要这一层：`analyze` 对每根 K 线都会给出一个描述（十字星、长阳……），
    但"今天收了根阳线"不是形态。这里要求至少一条硬证据，或者放量配合：
      反转确认（≥2 个信号）/ 吞没 / 跳空 / 放量长实体 / 放量长影线 / 放量突破。
    换句话说，页面上看到的每个标记都得有理由，而不是"随便挑一根也算"。
    """
    if result.get("reversal_up") or result.get("reversal_down"):
        return True
    volume_x = result.get("volume_x") or 0.0
    body = abs(result.get("body_pct") or 0.0)
    if (result.get("long_bull") or result.get("long_bear")) and body >= 2.5 and volume_x >= 1.5:
        return True
    if (result.get("bullish_engulf") or result.get("bearish_engulf")) and volume_x >= 1.2:
        return True
    if result.get("gap_up") or result.get("gap_down"):
        return True
    if (result.get("hammer") or result.get("shooting")) and volume_x >= 1.3:
        return True
    if (result.get("breakout_10") or result.get("breakdown_10")) and volume_x >= 1.5:
        return True
    return False
