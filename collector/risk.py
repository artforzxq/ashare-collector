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
    # 期望值口径：只看盈亏比是不够的——0.8 的盈亏比配 70% 胜率是赚的，
    # 3.0 的配 20% 胜率是亏的。所以这里用 期望(R) = 胜率×盈亏比 − (1−胜率)
    # 决定缩放到多少；期望 ≤ 0 直接不建仓。胜率由回测测出来（报告里会给建议值）。
    "win_rate": 0.5,
    "target_expectancy": 0.5,   # 期望达到多少给满缩放（单位：R）
    # 上方没有阻力带、且离 250 日高点还远时的兜底倍数：风控不能在"数据缺失"时放行。
    # 1.0 表示"只按 1 倍风险算收益空间"——配 50% 胜率时期望正好 0，也就是不做。
    "no_resistance_rr": 1.0,
    "open_space_atr_multiple": 3.0,   # 接近历史高点、上方真空时，用几倍 ATR 估目标
    # "接近 250 日高点"的容差（%）。实测强势票回撤到 -5%~-8% 很常见
    # （SZ000333 在 -5.4% 时仍被当成"上方有阻力"），容差太紧会把这批票系统性挡在门外。
    "open_space_high_tolerance_pct": 8.0,
    "cap_floor": 0.3,
    "max_stop_pct": 8.0,     # 止损离现价超过这个百分比就不做（位置太远，风险预算不够）
    # 换手放量折价：门槛 3 倍是按证据定的（2~3 倍那档 t≈−1.5 不够硬，>3 倍 t≈−3.1）
    "turnover_discount": {
        "enabled": True,
        "ratio": 3.0,
        "factor": 0.6,
        "high_position_pct": None,
    },
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


def turnover_discount(params: dict, row: dict, notes: list[str]) -> float | None:
    """换手放量时把仓位上限打折；不触发就返回 None。

    实证依据（`run.py turnover`，400 只标的、2.1 年、12.9 万样本，按交易日聚合）：
    换手 **2~3 倍**时 20 日超额 −0.38%（t = −1.53，不够硬）；
    **3 倍以上** −1.29%（t = −3.13，站得住）。放量之后偏弱这件事，证据集中在最极端那一档。

    所以这里**只缩仓位、不否决**：它是"少做一点"的证据，不是"不能做"。
    注意这是负向信号——按规格它进不了状态分（台账的转正判定只认正向 IC），
    放在风险层正合适：风险层本来就回答"做多大"。

    `high_position_pct` 是可选收窄条件（只在高位放量时打折）：**我们没测过它**，
    默认关着；想用就填 5（距 250 日高点 5% 以内）。
    """
    conf = params.get("turnover_discount") or {}
    if not conf.get("enabled"):
        return None
    ratio = row.get("turnover_ratio")
    if ratio is None:
        return None
    threshold = float(conf.get("ratio", 2.0))
    if float(ratio) < threshold:
        return None
    high_pct = conf.get("high_position_pct")
    if high_pct is not None:
        near = row.get("dist_to_high_250")
        if near is None or float(near) < -abs(float(high_pct)):
            return None
    factor = float(conf.get("factor", 0.6))
    notes.append(f"换手放量 {float(ratio):.1f} 倍（≥{threshold:g}）→ 仓位上限 ×{factor:g}"
                 f"（实证：这类放量后 5 日平均跑输同批标的）")
    return factor


def assess(row: dict, bands: list[dict], cfg: dict | None = None, candle: dict | None = None) -> dict:
    """算出一只标的当前的仓位上限与止损位。candle 是 K 线形态（candles.analyze 的结果）。"""
    params = _params(cfg or {})
    candle = candle or {}
    close = row.get("close")
    state = row.get("state") or "range"
    notes: list[str] = []
    blank = {"position_cap": 0.0, "stop_level": None, "stop_pct": None,
             "risk_reward": None, "expectancy": None, "reason": "", "notes": notes}

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
        # 结构位太远——**强势票的典型处境**（涨了很久，脚下支撑离现价很远）。
        # 因为"止损放不下"就整只票不做，等于系统性排除最强的标的：实测 SZ000333
        # 上升趋势持续 100 天、趋势分 77，却长期 0 仓，就是被这一条挡住的。
        # 改成：结构位太远时退到波动率止损，让"风险预算"去约束参与方式，而不是否决参与。
        atr = row.get("atr14")
        fallback = close - float(params["atr_stop_multiple"]) * float(atr) if atr else None
        if fallback is None or fallback >= close \
                or (close - fallback) / close * 100 > float(params["max_stop_pct"]):
            return {**blank, "reason": f"止损离现价 {risk_pct:.1f}%，结构位和 ATR 都超过 "
                                      f"{params['max_stop_pct']:g}% 的风险预算，0 仓"}
        notes.append(f"结构位太远（{risk_pct:.1f}%）→ 改用 "
                     f"{params['atr_stop_multiple']:g} 倍 ATR 止损")
        stop = fallback
        stop_reason = f"{params['atr_stop_multiple']:g} 倍 ATR（结构位太远）"
        risk_pct = (close - stop) / close * 100

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
    downside = close - stop
    if resistance:
        upside = (resistance["price_low"] + resistance["price_high"]) / 2 - close
    else:
        # 上方没有阻力带，有两种成因，不能一概而论：
        #   a) 价格贴着/突破 250 日高点——成交密集区都在脚下，上方本来就是真空，"没带"就是"空间打开"
        #   b) 关键带没识别出头顶那段——这是数据缺失，不是好消息
        # 以前这两种都走"保守 1R"，配上 50% 胜率期望正好是 0，于是 b) 还好，
        # a) 却被系统性判 0 仓：越强的票越做不了（实测 SZ000333 上升趋势 100 天、离高点 5.4%，长期 0 仓）。
        near_high = row.get("dist_to_high_250")
        atr = row.get("atr14")
        projection = float(params["open_space_atr_multiple"]) * float(atr) if atr else None
        if near_high is None or projection is None:
            # 连参照都没有 → 按保守倍数算收益空间，让缩放照常生效（config: risk.no_resistance_rr）
            upside = downside * float(params["no_resistance_rr"])
            notes.append(f"上方无阻力带、也缺高点参照 → 按 {params['no_resistance_rr']:g}R 保守处理")
        elif float(near_high) >= -abs(float(params["open_space_high_tolerance_pct"])):
            # (a) 贴近或突破 250 日高点：那个"高点"已经不是天花板了，用波动率估目标
            upside = projection
            notes.append(f"接近 250 日高点（{float(near_high):+.1f}%）→ 上方真空，"
                         f"按 {params['open_space_atr_multiple']:g} 倍 ATR 估目标")
        else:
            # (b) 离高点还远，头顶一定有筹码，只是密集区分箱没把它挑出来。
            # 用"到 250 日高点的距离"当参照，再让它不超过波动率给的空间——宁可低估，不凭空放大。
            room = close * (-float(near_high)) / 100.0
            upside = min(room, projection)
            notes.append(f"上方无阻力带 → 目标取「到 250 日高点 {room / close * 100:.1f}%」"
                         f"与「{params['open_space_atr_multiple']:g} 倍 ATR」的较小者")
    win_rate = _clamp(float(params["win_rate"]), 0.0, 1.0)
    expectancy = None
    if downside > 0 and upside > 0:
        reward_risk = round(upside / downside, 2)
        expectancy = round(win_rate * reward_risk - (1 - win_rate), 4)
        if expectancy <= 0:
            notes.append(
                f"盈亏比 {reward_risk:.2f} 配 {win_rate:.0%} 胜率 → 期望 {expectancy:+.2f}R，不做")
            return {**blank, "stop_level": round(stop, 4),
                    "stop_pct": round((stop / close - 1) * 100, 2),
                    "risk_reward": reward_risk,
                    "expectancy": expectancy,
                    "reason": f"止损放{stop_reason}；盈亏比 {reward_risk:.2f} × 胜率 {win_rate:.0%} "
                              f"→ 期望 {expectancy:+.2f}R，0 仓"}
        scale = _clamp(expectancy / float(params["target_expectancy"]),
                       float(params["cap_floor"]), 1.0)
        cap *= scale
        notes.append(f"盈亏比 {reward_risk:.2f} × 胜率 {win_rate:.0%} → "
                     f"期望 {expectancy:+.2f}R → 缩放 {scale:.2f}")
    elif upside <= 0:
        notes.append("上方紧贴阻力带，空间不足")

    # ---- 换手放量折价：只缩仓位，不否决 ----
    discount = turnover_discount(params, row, notes)
    if discount:
        cap *= discount

    return {
        "position_cap": round(_clamp(cap, 0.0, 1.0), 3),
        "stop_level": round(stop, 4),
        "stop_pct": round((stop / close - 1) * 100, 2),
        "risk_reward": reward_risk,
        "expectancy": expectancy,
        "reason": f"{state}趋势，止损放{stop_reason}"
                  + ("（反转确认，试探仓）" if state == "down" else "")
                  + (f"；换手放量 {float(row.get('turnover_ratio')):.1f} 倍 → 仓位上限 ×{discount:g}"
                     if discount else ""),
        "notes": notes,
    }


def describe(result: dict) -> str:
    """给简报用的一行摘要。"""
    if not result.get("stop_level") or result.get("position_cap", 0) <= 0:
        return result.get("reason", "不建议建仓")
    text = f"仓位上限 {result['position_cap']:.0%}，止损 {result['stop_level']:.3f}（{result['stop_pct']:+.2f}%）"
    if result.get("risk_reward"):
        text += f"，盈亏比 {result['risk_reward']:.2f}"
    # 打折的原因要跟着走：只看到"仓位变小了"却不知道为啥，等于把结论藏起来
    for note in result.get("notes") or []:
        if "换手放量" in note:
            text += "，" + note.split("（实证")[0].strip()
            break
    return text
