"""数据校验：双源交叉、跳变检测、缺失分级、质量标记。

原则：宁可少提醒，也不要在脏数据上做判断。质量标记会一路传到状态层，
由仲裁规则 R0 决定是否冻结当日全部自动提醒。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence


@dataclass
class MergeResult:
    rows: list[dict]
    quality_flag: str = "ok"          # ok / suspect
    conflicts: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class BarValidation:
    rows: list[dict]
    blocked: list[dict] = field(default_factory=list)      # 阻断写入，等人工确认
    anomalies: list[dict] = field(default_factory=list)    # 只标记，不阻断
    quality_flag: str = "ok"
    notes: list[str] = field(default_factory=list)


def _relative_diff(a: float | None, b: float | None) -> float:
    if a is None or b is None:
        return 0.0
    base = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / base


def merge_two_sources(primary_rows: Sequence[dict], backup_rows: Sequence[dict], cfg: dict) -> MergeResult:
    """以主源为基准合并备份源，逐日比对收盘价与成交额。"""
    tol_price = float(cfg["validation"].get("price_tol", 0.003))
    tol_amount = float(cfg["validation"].get("amount_tol", 0.03))

    backup_by_date = {row["trade_date"]: row for row in backup_rows}
    merged: list[dict] = []
    conflicts: list[dict] = []
    notes: list[str] = []

    for row in primary_rows:
        record = dict(row)
        record.setdefault("quality_flag", "ok")
        peer = backup_by_date.get(row["trade_date"])
        if peer:
            price_diff = _relative_diff(row.get("close"), peer.get("close"))
            amount_diff = _relative_diff(row.get("amount"), peer.get("amount"))
            if price_diff > tol_price or amount_diff > tol_amount:
                record["quality_flag"] = "suspect"
                conflicts.append(
                    {
                        "trade_date": row["trade_date"],
                        "code": row.get("code"),
                        "close": [row.get("close"), peer.get("close")],
                        "amount": [row.get("amount"), peer.get("amount")],
                        "price_diff": round(price_diff, 6),
                        "amount_diff": round(amount_diff, 6),
                    }
                )
        merged.append(record)

    primary_dates = {row["trade_date"] for row in primary_rows}
    for row in backup_rows:
        if row["trade_date"] in primary_dates:
            continue
        record = dict(row)
        record["quality_flag"] = "suspect"
        record["source"] = f"{row.get('source', 'backup')}(only)"
        merged.append(record)
        notes.append(f"{row['trade_date']} 仅备份源有数据，已按可疑写入")

    merged.sort(key=lambda r: r["trade_date"])
    flag = "suspect" if conflicts or notes else "ok"
    if conflicts:
        notes.append(f"双源冲突 {len(conflicts)} 处")
    return MergeResult(rows=merged, quality_flag=flag, conflicts=conflicts, notes=notes)


def validate_bars(
    rows: Sequence[dict],
    cfg: dict,
    is_st: bool = False,
    is_new: bool = False,
) -> BarValidation:
    """涨跌幅跳变阻断；成交额异常只标记。"""
    jump_limit = float(cfg["validation"].get("jump_pct_limit", 11.0))
    volume_ratio = float(cfg["validation"].get("volume_anomaly_ratio", 10.0))

    checked: list[dict] = []
    blocked: list[dict] = []
    anomalies: list[dict] = []
    amounts: list[float] = []

    for row in rows:
        record = dict(row)
        pct = record.get("pct_chg")
        if pct is not None and not is_st and not is_new and abs(float(pct)) > jump_limit:
            record["quality_flag"] = "blocked"
            blocked.append({"trade_date": record["trade_date"], "pct_chg": pct, "reason": "涨跌幅跳变"})
        else:
            amount = record.get("amount")
            if amount and len(amounts) >= 20:
                baseline = sum(amounts[-20:]) / 20.0
                if baseline > 0 and amount / baseline > volume_ratio:
                    anomalies.append(
                        {
                            "trade_date": record["trade_date"],
                            "amount": amount,
                            "ratio": round(amount / baseline, 2),
                            "reason": "成交额异常放大",
                        }
                    )
            if amount:
                amounts.append(float(amount))
        checked.append(record)

    notes = []
    if blocked:
        notes.append(f"跳变阻断 {len(blocked)} 日，等待人工确认")
    if anomalies:
        notes.append(f"成交额异常标记 {len(anomalies)} 日（可能是真信号，不阻断）")

    return BarValidation(
        rows=checked,
        blocked=blocked,
        anomalies=anomalies,
        quality_flag="blocked" if blocked else "ok",
        notes=notes,
    )


def grade_missing(missing_codes: Iterable[str], critical_codes: Iterable[str]) -> tuple[str, str]:
    """缺失分级：L1 单源缺失 / L2 双源缺失 / L3 关键标的缺失（冻结当日自动提醒）。"""
    missing = list(missing_codes)
    if not missing:
        return "ok", ""
    critical = set(critical_codes or [])
    hit = [code for code in missing if code in critical]
    if hit:
        return "L3", f"关键标的缺失：{hit} → 冻结当日全部自动提醒，只发数据异常通知"
    if len(missing) == 1:
        return "L1", f"单标的缺失：{missing} → 重试并切换备份源"
    return "L2", f"多标的缺失：{missing} → 状态判定沿用上一交易日结果"
