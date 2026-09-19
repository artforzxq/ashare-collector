"""提醒层：候选提醒生成、冷却期、每日预算、落库。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

from . import candles as candles_mod, db
from .levels import distance_pct, nearest_band

# 买入方向信号：会被风险否决与状态优先级影响
BUY_SIGNALS = {
    "STATE_TO_UP",
    "BREAKOUT_CONFIRMED",
    "PULLBACK_TO_SUPPORT",
    "RANGE_LOWER_EDGE",
    "SHELF_DETECTED",
}


@dataclass
class Candidate:
    code: str
    signal_type: str
    level: str
    price: float | None
    message: str
    state: str = "range"
    opportunity: float = 0.5
    period: str = "daily"
    payload: dict = field(default_factory=dict)


def build_candidates(feature_row: dict, prev_row: dict | None, bands: Sequence[dict], cfg: dict) -> list[Candidate]:
    """机会/风险层产出候选提醒。只描述事实，不做期望。"""
    code = feature_row["code"]
    close = feature_row.get("close")
    state = feature_row.get("state", "range")
    opportunity = feature_row.get("opportunity_score", 0.5)
    raw = feature_row.get("raw_values", {})
    candidates: list[Candidate] = []

    # 状态切换
    if prev_row and prev_row.get("state") and prev_row["state"] != state:
        if state == "down":
            candidates.append(
                Candidate(code, "STATE_TO_DOWN", "P0", close, f"状态转下跌趋势（{prev_row['state']} → {state}）", state, opportunity)
            )
        elif state == "up":
            candidates.append(
                Candidate(code, "STATE_TO_UP", "P1", close, f"状态转上升趋势（{prev_row['state']} → {state}）", state, opportunity)
            )
        else:
            candidates.append(
                Candidate(code, "STATE_TO_RANGE", "P2", close, f"状态转震荡（{prev_row['state']} → {state}）", state, opportunity)
            )

    support = nearest_band(bands, close, "support") if close else None
    resistance = nearest_band(bands, close, "resistance") if close else None

    # 风险否决：跌破最近的支撑带
    if support and close and close < support["price_low"] * 0.995:
        candidates.append(
            Candidate(
                code,
                "STOP_BREACH",
                "P0",
                close,
                f"跌破支撑带 {support['price_low']:.3f}–{support['price_high']:.3f}，形态失效",
                state,
                opportunity,
                payload={"band": support},
            )
        )

    # 回踩支撑带 / 区间下沿
    if support and close:
        gap = distance_pct(support, close)
        if gap is not None and abs(gap) <= 1.5:
            signal = "PULLBACK_TO_SUPPORT" if state == "up" else "RANGE_LOWER_EDGE"
            candidates.append(
                Candidate(
                    code,
                    signal,
                    "P1",
                    close,
                    f"价格进入支撑带 {support['price_low']:.3f}–{support['price_high']:.3f}（距带中心 {gap:+.2f}%）",
                    state,
                    opportunity,
                    payload={"band": support, "gap_pct": gap},
                )
            )

    # 放量突破确认
    if feature_row.get("breakout_confirmed"):
        candidates.append(
            Candidate(
                code,
                "BREAKOUT_CONFIRMED",
                "P1",
                close,
                f"放量突破确认（量比 {raw.get('vol_ratio_20')}）",
                state,
                opportunity,
            )
        )

    # 高位缩量蓄势
    consolidation_days = feature_row.get("consolidation_days") or 0
    shrink = feature_row.get("vol_shrink_ratio")
    ma60 = feature_row.get("ma60") or 0
    near_high = bool(close and ma60 and close > ma60 * 1.02)   # 位置必须在高位，排除长期窄幅下跌
    if 20 <= consolidation_days <= 60 and near_high and shrink is not None and shrink <= 0.6 and state != "down":
        candidates.append(
            Candidate(
                code,
                "SHELF_DETECTED",
                "P1",
                close,
                f"高位横盘蓄势 {consolidation_days} 日，量能萎缩至 {shrink:.2f} 倍",
                state,
                opportunity,
                payload={"consolidation_days": consolidation_days, "vol_shrink_ratio": shrink},
            )
        )

    # 量能异常（信息级）
    if (raw.get("vol_ratio_20") or 0) >= float(cfg["validation"].get("volume_anomaly_ratio", 10.0)):
        candidates.append(
            Candidate(code, "VOLUME_ANOMALY", "P2", close, f"成交额异常放大（{raw.get('vol_ratio_20')} 倍 20 日均值）", state, opportunity)
        )

    candidates.extend(key_pattern_candidates(feature_row, cfg))
    return candidates


def key_pattern_candidates(feature_row: dict, cfg: dict) -> list[Candidate]:
    """关键位置上的形态提醒——这一层才是形态真正被"用起来"的地方。

    为什么必须卡在关键位置：形态本身没有方向，位置才有。同样一根锤子，
    出现在 20 日新低附近的支撑上，和出现在一段没意义的横盘中间，含义完全不同。
    不做这道过滤的话，一年 250 根 K 线里有 6 成以上都能叫出某个形态名，
    提醒会变成噪音，看的人第一周就会全部忽略——那比不提醒更糟。

    级别只按**证据强度**分两档，不按方向分。定级用的是三条独立的门槛：
      · **状态前提**：看涨信号出现在上升趋势里只是延续，不是反转。实测不加这条时，
        "长阳 + 站上前 10 日高点"这种再普通不过的突破日会被算成"反转确认"，
        8 只标的 120 个交易日就响 221 次（约 4 个交易日一次），完全没法看。
      · **量能**：实测 79% 的候选是平量（量比 < 1.5）。没有量的形态，
        对"这里有没有人真的在干活"说明不了什么，只画在图上，不打扰。
      · **组合形态本身就是确认**：它已经把两三根 K 线的关系算进去了，不用再叠一条。
    三条合起来：反转 + 放量 → P1；组合形态或放量单根 → P2；其余沉默。

    过了这三道门槛的候选，还要过仲裁层那道**默认沉默**（机会分落在中性带内不发）。
    实测这一步再筛掉约一半——留着它：形态是"这里出现了什么"，值不值得打扰，
    仍然由机会层与仲裁层说了算，形态层不越权。
    最终频率（8 只标的 × 120 个交易日实测）：每只每 250 个交易日约 10 次。

    这里**不做买卖判断**：它说的是"这里出现了什么"，不是"该买还是该卖"。
    所以它也不进 BUY_SIGNALS——不参与方向性的风险否决与状态优先仲裁。
    """
    conf = cfg.get("alerts") or {}
    if not conf.get("key_pattern", True):
        return []
    candle = feature_row.get("candle") or {}
    if not candle.get("key") or not candles_mod.obvious(candle):
        return []

    # "放量"的门槛只有一份，就在形态层的阈值表里——两个地方各写一个 1.5，
    # 迟早会出现"形态层认为够硬、提醒层认为不够"这种自己跟自己打架的情况。
    volume_x = candle.get("volume_x") or 0.0
    loud = volume_x >= float(candles_mod.THRESHOLDS["volume_confirm_x"])
    combo = bool(candle.get("combo_up") or candle.get("combo_down"))
    state = feature_row.get("state", "range")
    up, down = bool(candle.get("reversal_up")), bool(candle.get("reversal_down"))
    reversal = (up and state != "up") or (down and state != "down")
    if not (loud or combo):
        return []                                  # 平量的单根形态：只画图，不打扰

    code = feature_row.get("code")
    close = feature_row.get("close")
    opportunity = feature_row.get("opportunity_score", 0.5)
    place = "、".join(candle.get("key_reasons") or []) or "关键位置"
    pattern = candle.get("pattern") or "形态"
    volume = f"，量 {volume_x:.2f} 倍" if volume_x >= 1.3 else ""
    payload = {"key_reasons": candle.get("key_reasons") or [], "pattern": pattern,
               "volume_x": candle.get("volume_x"), "combo": combo,
               "reversal": reversal, "state": state}

    if reversal and loud:
        direction = "反转向上确认" if up else "反转向下确认"
        return [Candidate(code, "KEY_PATTERN_STRONG", "P1", close,
                          f"关键位置（{place}）出现{direction}：{pattern}{volume}",
                          state, opportunity, payload=payload)]
    return [Candidate(code, "KEY_PATTERN", "P2", close,
                      f"关键位置（{place}）出现形态：{pattern}{volume}",
                      state, opportunity, payload=payload)]


def _days_between(later: str, earlier: str) -> float:
    fmt = "%Y-%m-%d %H:%M:%S" if len(later) > 10 else "%Y-%m-%d"
    try:
        return (datetime.strptime(later, fmt) - datetime.strptime(earlier, fmt)).total_seconds() / 86400.0
    except ValueError:
        return 999.0


def apply_cooldown(conn, candidates: Sequence[Candidate], cfg: dict, trade_date: str, now: str | None = None) -> tuple[list[Candidate], list[dict]]:
    """同一信号在冷却期内只发一次。"""
    cooldown = cfg["alerts"]
    # 按信号覆盖冷却期：默认那三档是"级别"口径，对有些信号不合适。
    # 关键位形态就是例子——P1 默认只隔 1 天，而一段突破行情会连着好几天都是
    # "放量长阳 + 站上前 10 日高点"，于是同一个信号连响四天。形态的意义在于
    # "它出现在哪里"，第一次就该看完，重复的只是同一件事在回声。
    overrides = cooldown.get("cooldown_days_by_signal") or {}
    now = now or db.now_iso()
    accepted: list[Candidate] = []
    suppressed: list[dict] = []

    for candidate in candidates:
        row = db.query_one(
            conn,
            "SELECT created_at, trade_date FROM alerts WHERE code=? AND signal_type=? ORDER BY created_at DESC LIMIT 1",
            (candidate.code, candidate.signal_type),
        )
        if row is None:
            accepted.append(candidate)
            continue

        days = overrides.get(candidate.signal_type)
        if days is not None and candidate.level != "P0":
            blocked = _days_between(trade_date, row["trade_date"]) < float(days)
        elif candidate.level == "P0":
            hours = _days_between(now, row["created_at"]) * 24
            blocked = hours < float(cooldown.get("cooldown_minutes_p0", 15)) / 60.0
        elif candidate.level == "P1":
            blocked = _days_between(trade_date, row["trade_date"]) < float(cooldown.get("cooldown_days_p1", 1))
        else:
            blocked = _days_between(trade_date, row["trade_date"]) < float(cooldown.get("cooldown_days_p2", 5))

        if blocked:
            suppressed.append({"candidate": candidate, "rule": "R5_COOLDOWN", "reason": f"冷却期内（上次 {row['created_at']}）"})
        else:
            accepted.append(candidate)
    return accepted, suppressed


def apply_budget(candidates: Sequence[Candidate], cfg: dict) -> tuple[list[Candidate], list[dict]]:
    """提醒预算：P0 不限；P1 每天最多 N 条；P2 每个标的每天合并成一条。"""
    budget_p1 = int(cfg["alerts"].get("budget_p1", 3))
    keep: list[Candidate] = []
    dropped: list[dict] = []

    p0 = [c for c in candidates if c.level == "P0"]
    p1 = sorted([c for c in candidates if c.level == "P1"], key=lambda c: c.opportunity, reverse=True)
    p2 = [c for c in candidates if c.level == "P2"]

    keep.extend(p0)
    keep.extend(p1[:budget_p1])
    for candidate in p1[budget_p1:]:
        dropped.append({"candidate": candidate, "rule": "R6_BUDGET", "reason": "超出当日 P1 预算，按机会分丢弃"})

    merged: dict[str, Candidate] = {}
    for candidate in p2:
        current = merged.get(candidate.code)
        if current is None or candidate.opportunity > current.opportunity:
            if current is not None:
                dropped.append({"candidate": current, "rule": "R6_BUDGET", "reason": "P2 合并为一条汇总"})
            merged[candidate.code] = candidate
        else:
            dropped.append({"candidate": candidate, "rule": "R6_BUDGET", "reason": "P2 合并为一条汇总"})
    keep.extend(merged.values())

    return keep, dropped


def persist(
    conn,
    candidates: Sequence[Candidate],
    suppressed_logs: Sequence[dict],
    trade_date: str,
    feature_version: str,
) -> int:
    now = db.now_iso()
    rows = []
    for candidate in candidates:
        rows.append(
            {
                "created_at": now,
                "trade_date": trade_date,
                "code": candidate.code,
                "level": candidate.level,
                "signal_type": candidate.signal_type,
                "state": candidate.state,
                "price": candidate.price,
                "message": candidate.message,
                "payload_json": db.dump_json(candidate.payload),
                "feature_version": feature_version,
                "arbitrated_by": candidate.payload.get("arbitrated_by"),
                "suppressed_by": candidate.payload.get("suppressed_by"),
            }
        )
    written = db.upsert_rows(conn, "alerts", rows, ["code", "trade_date", "signal_type", "level"])

    logs = []
    for item in suppressed_logs:
        candidate = item["candidate"]
        logs.append(
            {
                "created_at": now,
                "trade_date": trade_date,
                "code": candidate.code,
                "conflict_type": candidate.signal_type,
                "party_a": candidate.signal_type,
                "party_b": item.get("party_b", ""),
                "rule_applied": item["rule"],
                "decision": "suppressed",
                "suppressed_signal": candidate.message,
            }
        )
    if logs:
        # 按 (交易日, 标的, 信号, 规则, 裁决) 幂等写入：
        # 原来这里是裸 INSERT，同一个交易日重跑一次就多一行，
        # 复盘时"某条规则压了多少次"会被重复计数。
        db.upsert_rows(
            conn,
            "arbitration_log",
            logs,
            ["trade_date", "code", "conflict_type", "rule_applied", "decision"],
        )
    return written
