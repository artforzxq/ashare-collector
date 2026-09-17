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
