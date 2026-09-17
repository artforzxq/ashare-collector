"""风险层：回答"做多大、错了怎么办"。

规格里风险层的唯一出口是 `position_cap`（仓位上限）+ `stop_level`（止损位）。
它不做方向判断——方向是状态层的事；这一层只管在当前结论下能承受多大风险。

2026-09 的一次修正（很重要）：
原来"下跌趋势 → 一票否决 0 仓"。但状态机是**滞后**的（要从 45 爬到 70 还得连续确认），
用它单独否决，等于永远错过转折的第一波——真实案例：科创50ETF 在平台上出现放量长阳
（+4.27%，量 1.41 倍），状态仍然是"下跌趋势"，于是被判 0 仓。

现在的规则是**结构优先、形态确认**：
  1. 数据不可信 / 跌破支撑带下沿 / 止损离现价太远 → 仍然 0 仓
  2. 基准仓位按状态给，但"下跌趋势"允许**试探仓**——前提是出现反转确认
     （放量长阳、阳包阴、锤子、站上前 10 日高点、向上跳空，至少两个信号）
  3. 之后统一按波动率和盈亏比缩放
止损仍然放在结构位上（平台/支撑带下沿），这才是"错了就认"的位置。
"""

from __future__ import annotations

from .levels import nearest_band, support_for

DEFAULTS = {
    "base_cap": {"up": 0.8, "range": 0.4, "down": 0.0},   # 下跌趋势默认不建仓
    "probe_cap": 0.3,        # 出现反转确认时，下跌趋势里允许的试探仓位上限
    "target_atr_pct": 2.0,   # 目标波动率（ATR 占价格百分比）
    "stop_buffer_pct": 0.5,  # 止损放在支撑带下沿再往下留多少
    "atr_stop_multiple": 2.0,
    "min_reward_risk": 2.0,
    "cap_floor": 0.3,
    "max_stop_pct": 8.0,     # 止损离现价超过这个百分比就不做（位置太远，风险预算不够）
}


def _params(cfg: dict) -> dict:
    merged = dict(DEFAULTS)
    overrides = (cfg or {}).get("risk") or {}
    merged.update(overrides)
    base = dict(DEFAULTS["base_cap"])
    base.update(overrides.get("base_cap") or {})
    merged["base_cap"] = base
    return merged


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def assess(row: dict, bands: list[dict], cfg: dict | None = None, candle: dict | None = None) -> dict:
    """算出一只标的当前的仓位上限与止损位。candle 是 K 线形态（candles.analyze 的结果）。"""
    params = _params(cfg or {})
    candle = candle or {}
    close = row.get("close")
    state = row.get("state") or "range"
    notes: list[str] = []
    blank = {"position_cap": 0.0, "stop_level": None, "stop_pct": None,
             "risk_reward": None, "reason": "", "notes": notes}

    if (row.get("quality_flag") or row.get("data_quality_flag") or "ok") != "ok":
        return {**blank, "reason": "数据不可信，0 仓"}
    if not close:
        return {**blank, "reason": "没有收盘价"}

    # ---- 结构：脚下有没有平台 / 支撑 ----
    support = support_for(bands, close)
    if support and close < support["price_low"] * 0.995:
        notes.append(f"已跌破 {support['price_low']:.3f}")
        return {**blank, "reason": "跌破支撑/平台下沿，形态失效，0 仓"}

    if support:
        stop = support["price_low"] * (1 - float(params["stop_buffer_pct"]) / 100)
        stop_reason = "平台下沿" if support["level_type"] == "range" else "支撑带下沿"
    else:
        atr = row.get("atr14")
        if not atr:
            return {**blank, "reason": "既没有支撑带也没有 ATR，算不出止损"}
        stop = close - float(params["atr_stop_multiple"]) * float(atr)
        stop_reason = f"{params['atr_stop_multiple']:g} 倍 ATR"
    if stop >= close:
        atr = row.get("atr14") or close * 0.02
        stop = close - float(atr)
        stop_reason = "1 倍 ATR"

    risk_pct = (close - stop) / close * 100
    if risk_pct > float(params["max_stop_pct"]):
        return {**blank, "reason": f"止损离现价 {risk_pct:.1f}%（超过 {params['max_stop_pct']:g}%），位置太远，0 仓"}

    # ---- 基准仓位 ----
    reversal = bool(candle.get("reversal_up") or candle.get("long_bull"))
    if state == "down":
        if not reversal:
            # 0 仓就不报止损位：给了止损会让人以为"有个仓位在那儿"
            return {**blank, "reason": "下跌趋势且没有反转确认，先不建仓（等放量长阳 / 阳包阴 / 站上平台）"}
        cap = float(params["probe_cap"])
        notes.append(f"下跌趋势 + {candle.get('pattern') or '反转形态'} → 试探仓上限 {cap:.0%}")
    else:
        cap = float(params["base_cap"].get(state, 0.4))
        notes.append(f"{state} 基准仓位 {cap:.0%}")
        if reversal:
            cap = min(cap * 1.15, 0.5)
            notes.append(f"叠加 {candle.get('pattern')} → 上调到 {cap:.0%}")

    atr_pct = row.get("atr_pct")
    if atr_pct:
        scale = _clamp(float(params["target_atr_pct"]) / float(atr_pct), float(params["cap_floor"]), 1.0)
        cap *= scale
        notes.append(f"波动率 {atr_pct:.2f}% → 缩放 {scale:.2f}")

    reward_risk = None
    resistance = nearest_band(bands, close, "resistance")
    if resistance:
        upside = (resistance["price_low"] + resistance["price_high"]) / 2 - close
        downside = close - stop
        if downside > 0 and upside > 0:
            reward_risk = round(upside / downside, 2)
            scale = _clamp(reward_risk / float(params["min_reward_risk"]), float(params["cap_floor"]), 1.0)
            cap *= scale
            notes.append(f"盈亏比 {reward_risk:.2f} → 缩放 {scale:.2f}")
        elif upside <= 0:
            notes.append("上方紧贴阻力带，空间不足")

    return {
        "position_cap": round(_clamp(cap, 0.0, 1.0), 3),
        "stop_level": round(stop, 4),
        "stop_pct": round((stop / close - 1) * 100, 2),
        "risk_reward": reward_risk,
        "reason": f"{state}趋势，止损放{stop_reason}"
                  + ("（反转确认，试探仓）" if state == "down" else ""),
        "notes": notes,
    }


def describe(result: dict) -> str:
    """给简报用的一行摘要。"""
    if not result.get("stop_level") or result.get("position_cap", 0) <= 0:
        return result.get("reason", "不建议建仓")
    text = f"仓位上限 {result['position_cap']:.0%}，止损 {result['stop_level']:.3f}（{result['stop_pct']:+.2f}%）"
    if result.get("risk_reward"):
        text += f"，盈亏比 {result['risk_reward']:.2f}"
    return text
