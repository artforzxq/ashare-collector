"""指标台账：因子登记、归一化、类别预算校验。

角色只有三种：primary（主干，决定基础分）、modifier（修正，加减分）、veto（否决）。
status：candidate → shadow → active → retired。shadow 只记录贡献分，不参与打分。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

CATEGORY_BUDGET = {
    "trend": 0.40,
    "structure": 0.20,
    "volume": 0.25,
    "volatility": 0.15,
    "fund": 0.10,
    "sentiment": 0.10,
}


@dataclass
class Factor:
    factor_id: str
    name: str = ""
    layer: str = "state"
    role: str = "modifier"
    category: str = "trend"
    weight: float = 0.0
    min_samples: int = 60
    status: str = "candidate"
    source_field: str = ""
    normalize: dict = field(default_factory=dict)

    @property
    def participates(self) -> bool:
        """active 且权重不为 0 才参与打分。"""
        return self.status == "active" and self.weight > 0


def normalize_value(value: float | None, spec: dict) -> float | None:
    """把原始值压到 0..1。linear：x0→0，x1→1（x1 < x0 表示反向）；step：按阈值分桶。"""
    if value is None or spec is None:
        return None
    kind = spec.get("type", "linear")

    if kind == "linear":
        x0 = float(spec.get("x0", 0.0))
        x1 = float(spec.get("x1", 1.0))
        if x1 == x0:
            return 0.5
        score = (float(value) - x0) / (x1 - x0)
        return max(0.0, min(1.0, score))

    if kind == "step":
        thresholds = spec.get("thresholds", [])
        values = spec.get("values", [])
        for idx, edge in enumerate(thresholds):
            if value < edge and idx < len(values):
                return float(values[idx])
        return float(values[-1]) if values else None

    raise ValueError(f"未知的归一化类型：{kind}")


class FactorRegistry:
    def __init__(self, factor_dicts: Iterable[dict], feature_version: str = "v1"):
        self.factors: list[Factor] = []
        for raw in factor_dicts or []:
            known = {k: raw[k] for k in raw if k in Factor.__dataclass_fields__}
            self.factors.append(Factor(**known))
        self.feature_version = feature_version

    def by_id(self, factor_id: str) -> Factor | None:
        return next((f for f in self.factors if f.factor_id == factor_id), None)

    def layer_factors(self, layer: str, statuses: tuple[str, ...] = ("active",)) -> list[Factor]:
        return [f for f in self.factors if f.layer == layer and f.status in statuses]

    def active_state_factors(self) -> list[Factor]:
        return self.layer_factors("state", ("active",))

    def shadow_factors(self) -> list[Factor]:
        return [f for f in self.factors if f.status == "shadow"]

    def score_layer(self, layer: str, values: dict[str, float | None]) -> tuple[float | None, list[dict]]:
        """层内先合成一个数：Σ(weight × normalized) / Σ(weight) × 100。

        返回 (层分数, 贡献明细)。历史不足的因子从分母里剔除，并记录原因。
        """
        total_weight = 0.0
        total_score = 0.0
        contributions: list[dict] = []

        for factor in self.factors:
            if factor.layer != layer:
                continue
            raw_value = values.get(factor.source_field)
            normalized = normalize_value(raw_value, factor.normalize)
            participates = factor.participates and normalized is not None
            contribution = (factor.weight * normalized) if (participates and normalized is not None) else None

            if participates and contribution is not None:
                total_weight += factor.weight
                total_score += contribution

            contributions.append(
                {
                    "factor_id": factor.factor_id,
                    "role": factor.role,
                    "status": factor.status,
                    "category": factor.category,
                    "raw_value": raw_value,
                    "normalized_score": normalized,
                    "weight": factor.weight if factor.participates else 0.0,
                    "contribution": contribution,
                    "counted": bool(participates),
                    "note": "" if participates else ("历史不足" if raw_value is None else "未生效（影子/观察期）"),
                }
            )

        layer_score = (total_score / total_weight * 100.0) if total_weight > 0 else None
        return layer_score, contributions

    def category_budget_violations(self) -> list[str]:
        """按类别汇总 active 因子权重，检查是否超出预算。"""
        totals: dict[str, float] = {}
        for factor in self.factors:
            if factor.status != "active":
                continue
            totals[factor.category] = totals.get(factor.category, 0.0) + factor.weight

        problems: list[str] = []
        fund_sentiment = totals.get("fund", 0.0) + totals.get("sentiment", 0.0)
        if fund_sentiment > 0.10:
            problems.append(f"资金+情绪类权重合计 {fund_sentiment:.2f} 超过预算 0.10")
        for category, total in totals.items():
            if category in ("fund", "sentiment"):
                continue
            budget = CATEGORY_BUDGET.get(category)
            if budget is not None and total > budget + 1e-9:
                problems.append(f"{category} 类权重合计 {total:.2f} 超过预算 {budget:.2f}")
        return problems

    def weight_snapshot(self) -> dict[str, Any]:
        return {
            "feature_version": self.feature_version,
            "weights": {f.factor_id: f.weight for f in self.factors},
            "status": {f.factor_id: f.status for f in self.factors},
        }

    def sync_to_db(self, conn, effective_from: str) -> None:
        from . import db

        rows = []
        for factor in self.factors:
            rows.append(
                {
                    "factor_id": factor.factor_id,
                    "name": factor.name,
                    "layer": factor.layer,
                    "role": factor.role,
                    "category": factor.category,
                    "weight": factor.weight,
                    "min_samples": factor.min_samples,
                    "status": factor.status,
                    "feature_version": self.feature_version,
                    "added_date": effective_from,
                }
            )
        db.upsert_rows(conn, "factor_registry", rows, ["factor_id"])
        db.upsert_rows(
            conn,
            "rule_version",
            [
                {
                    "feature_version": self.feature_version,
                    "effective_from": effective_from,
                    "change_note": "初始化台账",
                    "weights_snapshot": db.dump_json(self.weight_snapshot()),
                }
            ],
            ["feature_version"],
        )
