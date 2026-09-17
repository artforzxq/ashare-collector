"""仲裁层：把冲突裁决写死在规则表里，运行时只做匹配，不改规则。

优先级链：数据健康 → 风险 → 状态 → 机会 → 提醒。
默认沉默：意见不一致时不动。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .alerts import BUY_SIGNALS, Candidate


@dataclass
class DecisionContext:
    trade_date: str
    code: str = ""
    quality_flag: str = "ok"          # ok / suspect / stale / blocked
    state: str = "range"
    risk_veto: bool = False            # 风险层是否一票否决
    neutral_band: float = 0.10         # 默认沉默带
    cross_period_conflict: bool = False


@dataclass
class Decision:
    accepted: list[Candidate] = field(default_factory=list)
    suppressed: list[dict] = field(default_factory=list)
    logs: list[dict] = field(default_factory=list)


def arbitrate(candidates: Sequence[Candidate], ctx: DecisionContext) -> Decision:
    decision = Decision()
    for candidate in candidates:
        outcome = _apply_rules(candidate, ctx, decision.logs)
        if outcome["action"] == "drop":
            decision.suppressed.append(
                {"candidate": candidate, "rule": outcome["rule"], "reason": outcome["reason"], "party_b": outcome.get("party_b", "")}
            )
            continue
        if outcome["action"] == "downgrade":
            candidate.level = outcome["new_level"]
            candidate.payload["suppressed_by"] = outcome["rule"]
        candidate.payload["arbitrated_by"] = outcome["rule"]
        decision.accepted.append(candidate)
    return decision


def _log(logs: list[dict], rule: str, candidate: Candidate, reason: str, party_b: str = "") -> None:
    logs.append(
        {
            "rule": rule,
            "candidate": candidate,
            "reason": reason,
            "party_b": party_b,
        }
    )


def _apply_rules(candidate: Candidate, ctx: DecisionContext, logs: list[dict]) -> dict:
    # R0 数据健康：数据可疑或缺失时冻结全部自动提醒（数据异常通知除外）
    if ctx.quality_flag != "ok" and candidate.signal_type != "DATA_ANOMALY":
        _log(logs, "R0_DATA_HEALTH", candidate, f"数据质量={ctx.quality_flag}，冻结当日自动提醒")
        return {"action": "drop", "rule": "R0_DATA_HEALTH", "reason": f"数据质量={ctx.quality_flag}", "party_b": "数据层"}

    # R1 风险否决：风险层一票否决所有买入方向信号
    if ctx.risk_veto and candidate.signal_type in BUY_SIGNALS:
        _log(logs, "R1_RISK_VETO", candidate, "风险层否决，机会降级为仅记录", "风险层")
        return {"action": "drop", "rule": "R1_RISK_VETO", "reason": "风险否决", "party_b": "风险层"}

    # R2 状态优先：下跌状态里所有买入信号降级为 P2 提示
    if ctx.state == "down" and candidate.signal_type in BUY_SIGNALS and candidate.level != "P2":
        _log(logs, "R2_STATE_PRIORITY", candidate, "下跌趋势中，买入信号降级为 P2", "状态层")
        return {"action": "downgrade", "rule": "R2_STATE_PRIORITY", "new_level": "P2", "reason": "状态优先", "party_b": "状态层"}

    # R3 默认沉默：机会分落在中性带内判为不确定，不发提醒
    if candidate.level != "P0" and abs(candidate.opportunity - 0.5) < ctx.neutral_band:
        _log(logs, "R3_NEUTRAL_SILENCE", candidate, f"机会分 {candidate.opportunity:.2f} 落在不确定带内", "机会层")
        return {"action": "drop", "rule": "R3_NEUTRAL_SILENCE", "reason": "不确定，默认沉默", "party_b": "机会层"}

    # R4 跨周期：日线与分钟级矛盾时以日线为准
    if ctx.cross_period_conflict and candidate.period != "daily":
        _log(logs, "R4_CROSS_PERIOD", candidate, "跨周期矛盾，日线优先", "状态层")
        return {"action": "drop", "rule": "R4_CROSS_PERIOD", "reason": "跨周期矛盾，日线优先", "party_b": "状态层"}

    # R5 P0 无条件通过（数据异常与风险事件必须送达）
    if candidate.level == "P0":
        return {"action": "accept", "rule": "R5_P0_PASS"}

    return {"action": "accept", "rule": "R6_PASS"}
