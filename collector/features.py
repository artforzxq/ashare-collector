"""特征计算与状态机。

特征全部用前复权价计算；窗口一律取"当日之前"，不含当日，避免未来数据。
状态判定用迟滞 + 确认 + 最短持续期三件套，避免状态来回抖动。
"""

from __future__ import annotations

from bisect import bisect_right
from typing import Iterable, Sequence

# 摆动点（分型）的左右确认根数。右边要等这么多根才算确认，所以最近 span 根永远不是摆动点——
# 这是"不用未来函数"的代价，也是它可信的原因。
SWING_SPAN = 2


# ---------- 基础序列工具 ----------

def sma(values: Sequence[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    window: list[float] = []
    for index, value in enumerate(values):
        window.append(value)
        if len(window) > n:
            window.pop(0)
        if len(window) == n and all(v is not None for v in window):
            out[index] = sum(window) / n
    return out


def _prior_stats(values: Sequence[float], index: int, n: int) -> tuple[float | None, float | None]:
    """当日之前 n 根的均值与标准差（不含当日）。"""
    if index < n:
        return None, None
    window = [v for v in values[index - n:index] if v is not None]
    if len(window) < n:
        return None, None
    mean = sum(window) / len(window)
    variance = sum((v - mean) ** 2 for v in window) / len(window)
    return mean, variance ** 0.5


def _prior_mean_tolerant(
    values: Sequence[float | None], index: int, n: int, min_ratio: float = 0.6
) -> tuple[float | None, float | None]:
    """当日之前 n 根的均值，**允许缺值**。

    换手率只有部分数据源给（腾讯、新浪都不给），本地大约四分之一的行是空的。
    用严格的 _prior_stats，缺一天就把整个窗口判成不可用——那会让这个因子的覆盖率
    低到没法用。所以这里按"窗口里有几个有效值"算，覆盖率一起返回，供体检判断。
    """
    if index < n:
        return None, None
    window = [v for v in values[index - n:index] if v is not None]
    if not window:
        return None, 0.0
    coverage = len(window) / n
    if coverage < min_ratio:
        return None, round(coverage, 4)
    return sum(window) / len(window), round(coverage, 4)


def atr_series(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], n: int = 14) -> list[float | None]:
    length = len(closes)
    out: list[float | None] = [None] * length
    if length <= n:
        return out
    trs = [highs[0] - lows[0]]
    for i in range(1, length):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    prev = sum(trs[:n]) / n
    out[n - 1] = prev
    for i in range(n, length):
        prev = (prev * (n - 1) + trs[i]) / n
        out[i] = prev
    return out


def adx_series(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], n: int = 14) -> list[float | None]:
    """Wilder ADX 近似实现。"""
    length = len(closes)
    out: list[float | None] = [None] * length
    if length < 2 * n + 1:
        return out

    plus = [0.0] * length
    minus = [0.0] * length
    tr = [0.0] * length
    for i in range(1, length):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus[i] = down_move if (down_move > up_move and down_move > 0) else 0.0
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))

    tr_s = sum(tr[1:n + 1])
    plus_s = sum(plus[1:n + 1])
    minus_s = sum(minus[1:n + 1])
    dxs: list[float] = []

    for i in range(n + 1, length):
        tr_s = tr_s - tr_s / n + tr[i]
        plus_s = plus_s - plus_s / n + plus[i]
        minus_s = minus_s - minus_s / n + minus[i]
        if tr_s <= 0:
            continue
        pdi = 100.0 * plus_s / tr_s
        mdi = 100.0 * minus_s / tr_s
        denominator = pdi + mdi
        dxs.append(100.0 * abs(pdi - mdi) / denominator if denominator else 0.0)
        if len(dxs) >= n:
            out[i] = sum(dxs[-n:]) / n
    return out


# ---------- 因子原始值 ----------

def _sign(value: float | None) -> float:
    if value is None:
        return 0.0
    return 1.0 if value > 0 else (-1.0 if value < 0 else 0.0)


# ---------- 摆动结构 ----------

# 均线是"平滑后的价格"，摆动结构是**没被平滑过的原始序列**：更高的高点 + 更高的低点。
# 趋势的定义本来就是这个，只是均线把它糊掉了。两条线一起看，才知道
# "趋势分在涨"到底是结构真的在抬升，还是只是几根阳线把均值拉上去了。


def _swings(values: Sequence[float], span: int = SWING_SPAN, kind: str = "low") -> tuple[list[int], list[float]]:
    """摆动点：左右各 span 根都不更低（更高）才算一个拐点。

    返回 (日期下标, 价格) 两条平行列表，下标天然递增，后面用二分查"到某天为止的最后一个"。
    """
    days: list[int] = []
    prices: list[float] = []
    for index in range(span, len(values) - span):
        window = values[index - span:index + span + 1]
        extreme = min(window) if kind == "low" else max(window)
        if values[index] == extreme:
            days.append(index)
            prices.append(float(values[index]))
    return days, prices


def _structure_at(swing_lows: tuple[list[int], list[float]],
                  swing_highs: tuple[list[int], list[float]],
                  index: int, span: int = SWING_SPAN) -> dict:
    """到第 index 根为止，**已经确认**的结构状态。

    返回 {swing_state, swing_low_1, swing_high_1, bars_since_swing_low,
          dist_to_swing_low, swing_low_2, swing_high_2}。
    swing_state：1 = 高低点同时抬升（HH+HL，最原始的多头结构），
                 -1 = 同时下移（LH+LL），0 = 混合，None = 摆动点还不够两个。
    """
    blanks = {"swing_state": None, "swing_low_1": None, "swing_high_1": None,
              "swing_low_2": None, "swing_high_2": None,
              "bars_since_swing_low": None, "dist_to_swing_low": None}
    low_days, low_prices = swing_lows
    high_days, high_prices = swing_highs
    last_confirmed_low = bisect_right(low_days, index - span) - 1
    last_confirmed_high = bisect_right(high_days, index - span) - 1
    if last_confirmed_low < 0 or last_confirmed_high < 0:
        return blanks
    low_index, low_price = low_days[last_confirmed_low], low_prices[last_confirmed_low]
    high_index, high_price = high_days[last_confirmed_high], high_prices[last_confirmed_high]
    output = dict(blanks)
    output["swing_low_1"] = low_price
    output["swing_high_1"] = high_price
    output["bars_since_swing_low"] = index - low_index
    if last_confirmed_low >= 1 and last_confirmed_high >= 1:
        prior_low = low_prices[last_confirmed_low - 1]
        prior_high = high_prices[last_confirmed_high - 1]
        output["swing_low_2"] = prior_low
        output["swing_high_2"] = prior_high
        rising = low_price > prior_low and high_price > prior_high
        falling = low_price < prior_low and high_price < prior_high
        output["swing_state"] = 1.0 if rising else (-1.0 if falling else 0.0)
    return output


def compute_feature_series(
    bars: Sequence[dict],
    cfg: dict,
    registry,
    extra: dict | None = None,
) -> list[dict]:
    """逐日计算特征、因子贡献与状态。bars 必须按日期升序且已通过校验。"""
    extra = extra or {}
    breadth_by_date = extra.get("breadth_score", {})
    share_by_date = extra.get("etf_share_chg", {})
    board_by_date = extra.get("max_boards", {})
    params = cfg.get("state", {})

    closes = [float(b.get("close_adj") or b.get("close")) for b in bars]
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    # 量能一律用"成交额"；指数拿不到成交额（成交量 × 点位不是金额），退回用成交量。
    # 同一个标的内部口径统一，所以量比、z-score 这些相对指标仍然可比。
    amounts = [float(b.get("amount") or b.get("volume") or 0.0) for b in bars]
    pcts = [b.get("pct_chg") for b in bars]
    # 换手率：只有 baostock 给（腾讯、新浪都不给），所以往下走的时候要容忍缺值
    turnovers: list[float | None] = []
    for bar in bars:
        value = bar.get("turnover_rate")
        try:
            turnovers.append(float(value) if value is not None else None)
        except (TypeError, ValueError):
            turnovers.append(None)

    ma20 = sma(closes, 20)
    ma60 = sma(closes, 60)
    ma120 = sma(closes, 120)
    atr14 = atr_series(highs, lows, closes, 14)
    adx14 = adx_series(highs, lows, closes, 14)
    swing_lows = _swings(lows, SWING_SPAN, "low")
    swing_highs = _swings(highs, SWING_SPAN, "high")

    rows: list[dict] = []
    for index, bar in enumerate(bars):
        close = closes[index]
        raw: dict[str, float | None] = {}

        raw["ma_slope_20"] = (
            round((ma20[index] / ma20[index - 20] - 1) * 100, 4)
            if index >= 20 and ma20[index] and ma20[index - 20]
            else None
        )

        if ma20[index] and ma60[index] and ma120[index]:
            if ma20[index] > ma60[index] > ma120[index]:
                raw["ma_align"] = 1.0
            elif ma20[index] < ma60[index] < ma120[index]:
                raw["ma_align"] = -1.0
            else:
                raw["ma_align"] = 0.0
        else:
            raw["ma_align"] = None

        raw["atr_pct"] = round(atr14[index] / close * 100, 4) if atr14[index] and close else None
        raw["adx14"] = round(adx14[index], 4) if adx14[index] is not None else None

        mean20, std20 = _prior_stats(amounts, index, 20)
        mean60_amount, _ = _prior_stats(amounts, index, 60)
        amount_z = round((amounts[index] - mean20) / std20, 4) if (mean20 and std20) else None
        raw["amount_zscore"] = amount_z
        raw["vol_ratio_20"] = round(amounts[index] / mean20, 4) if mean20 else None
        # 成交额的绝对水平（元）：小票过滤和仓位上限都要用，所以单独留一列。
        # 注意窗口不含当日，与文件内其它指标口径一致。
        raw["avg_amount_20d"] = round(mean20, 2) if mean20 else None
        raw["avg_amount_60d"] = round(mean60_amount, 2) if mean60_amount else None
        raw["vol_confirm"] = (
            round(max(-2.0, min(2.0, amount_z)) * _sign(pcts[index]), 4) if amount_z is not None else None
        )

        # 换手率的三个读法（都是影子因子，先只记录不参与打分）：
        #   turnover_rate  当日换手率
        #   turnover_20d   这只票自己的"常态"（前 20 日均值，允许缺值）
        #   turnover_ratio 今日 / 常态 —— 与 vol_ratio_20 同思路，但换手率剔除了股本规模的干扰：
        #                  大盘股天生换手低、小盘股天然高，拿绝对值横比没有意义
        turn = turnovers[index]
        turn_mean, turn_cover = _prior_mean_tolerant(turnovers, index, 20)
        raw["turnover_rate"] = round(turn, 4) if turn is not None else None
        raw["turnover_20d"] = round(turn_mean, 4) if turn_mean else None
        raw["turnover_ratio"] = round(turn / turn_mean, 4) if (turn and turn_mean) else None
        raw["turnover_coverage"] = turn_cover

        # 摆动结构：更高的高点 + 更高的低点，是趋势最原始的定义。
        # 只认**已经确认**的拐点（右边要等 SWING_SPAN 根），所以不会用到未来数据。
        structure = _structure_at(swing_lows, swing_highs, index)
        raw["swing_state"] = structure["swing_state"]
        raw["swing_low_1"] = structure["swing_low_1"]
        raw["swing_high_1"] = structure["swing_high_1"]
        raw["dist_to_swing_low"] = (
            round((close - structure["swing_low_1"]) / close * 100, 4)
            if structure["swing_low_1"] else None
        )
        raw["bars_since_swing_low"] = structure["bars_since_swing_low"]

        if index >= 20:
            prior_high = max(highs[index - 20:index])
            prior_low = min(lows[index - 20:index])
            raw["donchian_break"] = 1.0 if close > prior_high else (-1.0 if close < prior_low else 0.0)
            range_position = (close - prior_low) / (prior_high - prior_low) if prior_high > prior_low else 0.5
            raw["range_width_pct"] = round((prior_high - prior_low) / close * 100, 4)
        else:
            prior_high = prior_low = None
            raw["donchian_break"] = None
            raw["range_width_pct"] = None
            range_position = 0.5

        if index >= 250:
            raw["dist_to_high_250"] = round((close / max(highs[index - 250:index]) - 1) * 100, 4)
        else:
            raw["dist_to_high_250"] = None

        # 关键价代理：20 日高低点与 MA60 中距离最近者
        candidates = [value for value in (prior_high, prior_low, ma60[index]) if value]
        if candidates:
            nearest = min(candidates, key=lambda level: abs(level - close))
            raw["dist_to_level"] = round((close - nearest) / close * 100, 4)
        else:
            raw["dist_to_level"] = None

        # 蓄势识别：振幅收敛 + 量能萎缩
        consolidation_days = 0
        max_consolidation = 60
        for lookback in range(index, 19, -1):
            if consolidation_days >= max_consolidation:
                break
            window_high = max(highs[lookback - 20:lookback + 1])
            window_low = min(lows[lookback - 20:lookback + 1])
            width_pct = (window_high - window_low) / close * 100 if close else 999
            if width_pct <= 12.0:
                consolidation_days += 1
            else:
                break
        mean5, _ = _prior_stats(amounts, index + 1, 5) if index + 1 <= len(amounts) else (None, None)
        mean60, _ = _prior_stats(amounts, index + 1, 60) if index + 1 <= len(amounts) else (None, None)
        vol_shrink = round(mean5 / mean60, 4) if (mean5 and mean60) else None

        raw["breadth_score"] = breadth_by_date.get(bar["trade_date"])
        raw["etf_share_chg"] = share_by_date.get(bar["trade_date"])
        raw["max_boards"] = board_by_date.get(bar["trade_date"])

        score, contributions = registry.score_layer("state", raw)
        breakout_confirmed = 1 if (raw["donchian_break"] == 1.0 and (raw["vol_ratio_20"] or 0) >= 1.5) else 0

        rows.append(
            {
                "code": bar["code"],
                "trade_date": bar["trade_date"],
                "close": close,
                "ma20": round(ma20[index], 4) if ma20[index] else None,
                "ma60": round(ma60[index], 4) if ma60[index] else None,
                "ma120": round(ma120[index], 4) if ma120[index] else None,
                "ma_slope_20": raw["ma_slope_20"],
                "ma_align": raw["ma_align"],
                "adx14": raw["adx14"],
                "atr14": round(atr14[index], 4) if atr14[index] else None,
                "atr_pct": raw["atr_pct"],
                "vol_ratio_20": raw["vol_ratio_20"],
                "amount_zscore": raw["amount_zscore"],
                "avg_amount_20d": raw["avg_amount_20d"],
                "avg_amount_60d": raw["avg_amount_60d"],
                "turnover_rate": raw["turnover_rate"],
                "turnover_20d": raw["turnover_20d"],
                "turnover_ratio": raw["turnover_ratio"],
                "turnover_coverage": raw["turnover_coverage"],
                "swing_state": raw["swing_state"],
                "swing_low_1": raw["swing_low_1"],
                "swing_high_1": raw["swing_high_1"],
                "dist_to_swing_low": raw["dist_to_swing_low"],
                "bars_since_swing_low": raw["bars_since_swing_low"],
                "dist_to_high_250": raw["dist_to_high_250"],
                "donchian_break": raw["donchian_break"],
                "consolidation_days": consolidation_days,
                "range_width_pct": raw["range_width_pct"],
                "vol_shrink_ratio": vol_shrink,
                "breakout_confirmed": breakout_confirmed,
                "trend_score": round(score, 4) if score is not None else None,
                "range_score": round(100 - abs(score - 50) * 2, 4) if score is not None else None,
                "raw_values": raw,
                "contributions": contributions,
                "range_position": round(range_position, 4),
                "quality_flag": bar.get("quality_flag", "ok"),
            }
        )

    _apply_state_machine(rows, params)
    for row in rows:
        row["opportunity_score"] = round(_opportunity_score(row), 4)
    return rows


# ---------- 状态机 ----------

def classify(score: float, current_state: str, params: dict) -> str:
    """带迟滞的状态归类：进入阈值高、退出阈值低，中间是过渡带。"""
    enter_up = float(params.get("enter_up", 70))
    exit_up = float(params.get("exit_up", 55))
    enter_down = float(params.get("enter_down", 30))
    exit_down = float(params.get("exit_down", 45))

    if current_state == "up":
        if score <= exit_up:
            return "range" if score > enter_down else "down"
        return "up"
    if current_state == "down":
        if score >= exit_down:
            return "up" if score >= enter_up else "range"
        return "down"
    if score >= enter_up:
        return "up"
    if score <= enter_down:
        return "down"
    return "range"


def _apply_state_machine(rows: list[dict], params: dict) -> None:
    confirm_days = int(params.get("confirm_days", 2))
    min_state_days = int(params.get("min_state_days", 3))

    state = "range"
    state_since = rows[0]["trade_date"] if rows else None
    state_days = 0
    pending_days = 0
    candidate_start = None

    for row in rows:
        score = row.get("trend_score")
        switched = False

        if score is None:
            state_days += 1
        else:
            candidate = classify(score, state, params)
            if candidate == state:
                pending_days = 0
                candidate_start = None
                state_days += 1
            else:
                if pending_days == 0:
                    candidate_start = row["trade_date"]
                pending_days += 1
                if pending_days >= confirm_days and state_days >= min_state_days:
                    state = candidate
                    state_since = candidate_start or row["trade_date"]
                    state_days = pending_days
                    pending_days = 0
                    candidate_start = None
                    switched = True
                else:
                    state_days += 1

        row["state"] = state
        row["state_since"] = state_since
        row["state_days"] = state_days
        row["state_switched"] = switched
        row["pending_days"] = pending_days


def _opportunity_score(row: dict) -> float:
    """机会层第一版规则（后续按回测调参，不在这里塞更多指标）。"""
    state = row.get("state", "range")
    raw = row.get("raw_values", {})
    dist_to_level = raw.get("dist_to_level")
    vol_ratio = raw.get("vol_ratio_20") or 1.0

    if state == "up":
        score = 0.60
        if dist_to_level is not None and abs(dist_to_level) <= 1.5:
            score = 0.72           # 回踩到关键带附近
        if row.get("breakout_confirmed"):
            score = 0.78           # 放量突破确认
        return score
    if state == "down":
        return 0.35

    score = 0.50
    position = row.get("range_position", 0.5)
    if position <= 0.25:
        score = 0.62               # 区间下沿
    elif position >= 0.75:
        score = 0.40               # 区间上沿，性价比低
    if vol_ratio >= 2.0:
        score += 0.03
    return max(0.0, min(1.0, score))
