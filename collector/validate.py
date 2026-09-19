"""数据校验：双源交叉、跳变检测、缺失分级、质量标记。

原则：宁可少提醒，也不要在脏数据上做判断。质量标记会一路传到状态层，
由仲裁规则 R0 决定是否冻结当日全部自动提醒。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from . import limits


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


def _pct_of(row: dict) -> float | None:
    """当日涨跌幅：优先用 pct_chg，没有就用 收盘/前收 现算。"""
    value = row.get("pct_chg")
    if value is not None:
        return float(value)
    close, pre_close = row.get("close"), row.get("pre_close")
    if close and pre_close:
        return (float(close) / float(pre_close) - 1) * 100
    return None


def _pct_points(a: float | None, b: float | None) -> float | None:
    """两个涨跌幅差几个百分点；算不出来返回 None。"""
    if a is None or b is None:
        return None
    return abs(a - b)


def merge_two_sources(primary_rows: Sequence[dict], backup_rows: Sequence[dict], cfg: dict) -> MergeResult:
    """以主源为基准合并备份源，逐日比对**涨跌幅**与成交额。

    为什么不比绝对价：前复权价是相对"今天"倒推的，两个源的复权因子只要差一点点，
    越久的历史差得越多——实测同一天两源收盘价 0.000% 一致，但往前几百个交易日会出现
    成片"冲突"。那是复权口径差异，不是数据错误。所以横比看涨跌幅（复权无关），
    绝对价不一致而涨跌幅一致时只记一条说明。
    """
    tol_price = float(cfg["validation"].get("price_tol", 0.003))
    tol_amount = float(cfg["validation"].get("amount_tol", 0.03))
    tol_pct = float(cfg["validation"].get("pct_tol", 0.3))     # 单位：百分点
    # 有一边是估算值时（腾讯日线不返回成交额），用宽松容差——估算误差不该算数据冲突
    tol_amount_estimated = float(cfg["validation"].get("amount_tol_estimated", 0.15))

    backup_by_date = {row["trade_date"]: row for row in backup_rows}
    merged: list[dict] = []
    conflicts: list[dict] = []
    notes: list[str] = []
    # 主源没有、备份源有的字段，可以按白名单补齐。
    # 为什么需要这一步：换手率只有 baostock 给（腾讯、新浪都不给），而合并以主源为基准，
    # 不补的话数据走腾讯的那些天换手率就是空的，基于它的因子会一格一格断档。
    # 白名单只放"与复权基准无关"的比率类字段——价格、成交量这些绝不能混源，
    # 一混就等于在一段序列里换了把尺子。
    fill_fields = [str(name) for name in (cfg["validation"].get("fill_fields") or [])]
    filled: dict[str, int] = {}

    for row in primary_rows:
        record = dict(row)
        record.setdefault("quality_flag", "ok")
        peer = backup_by_date.get(row["trade_date"])
        if peer:
            price_diff = _relative_diff(row.get("close"), peer.get("close"))
            level_diff = price_diff                       # 绝对价差：只用来判断"复权口径不同"
            pct_diff = _pct_points(_pct_of(row), _pct_of(peer))
            # 主源那天派生的涨跌幅不可能超过涨跌停上限（实测腾讯在除权日会拿错前收，
            # 算出 +53% 这种数），而备份源正常 → 拿备份源的涨跌幅把主源修回来。
            # 价格仍用主源（保持复权基准统一），前收按修正后的涨跌幅反推，序列依然自洽。
            pct_self, pct_other = _pct_of(row), _pct_of(peer)
            board_limit = limits.limit_pct(str(row.get("code") or "")) + 1.0
            if (pct_self is not None and abs(pct_self) > board_limit
                    and pct_other is not None and abs(pct_other) <= board_limit
                    and row.get("close")):
                fixed = float(pct_other)
                record["pct_chg"] = round(fixed, 4)
                record["pre_close"] = round(float(row["close"]) / (1 + fixed / 100), 4)
                record["quality_flag"] = "suspect"
                notes.append(
                    f"{row['trade_date']} 主源涨跌幅 {pct_self:+.2f}% 超出涨跌停上限"
                    f"（前收不可靠），已按备份源的 {fixed:+.2f}% 修正"
                )
                pct_diff = _pct_points(fixed, pct_other)
            # 横比看涨跌幅（复权无关）；两边都算不出涨跌幅才退回比绝对价
            price_bad = (pct_diff > tol_pct) if pct_diff is not None else (level_diff > tol_price)
            amount_diff = _relative_diff(row.get("amount"), peer.get("amount"))
            if not price_bad and amount_diff <= tol_amount and level_diff > tol_price:
                notes.append(
                    f"{row['trade_date']} 两源绝对价差 {level_diff * 100:.2f}%（涨跌幅一致）"
                    "—— 复权基准不同，不是数据错误"
                )
            # 只有两条都不一致才算冲突：
            #   涨跌幅差 → 可能是复权口径差异，也可能是某一源的前收算错了（实测腾讯在除权日会错）
            #   绝对价差 → 也可能只是复权基准不同
            # 单独命中任一条都可能是"口径问题而非数据问题"，两条同时命中才值得报警。
            estimated = bool(row.get("amount_estimated") or peer.get("amount_estimated"))
            amount_limit = tol_amount_estimated if estimated else tol_amount
            if (price_bad and level_diff > tol_price) or amount_diff > amount_limit:
                record["quality_flag"] = "suspect"
                conflicts.append(
                    {
                        "trade_date": row["trade_date"],
                        "code": row.get("code"),
                        "close": [row.get("close"), peer.get("close")],
                        "amount": [row.get("amount"), peer.get("amount")],
                        "price_diff": round(price_diff, 6),
                        "pct_diff": round(pct_diff, 6) if pct_diff is not None else None,
                        "level_diff": round(level_diff, 6),
                        "amount_diff": round(amount_diff, 6),
                    }
                )
        if peer and fill_fields:
            for field in fill_fields:
                if record.get(field) is None and peer.get(field) is not None:
                    record[field] = peer[field]
                    filled[field] = filled.get(field, 0) + 1
        merged.append(record)

    primary_dates = {row["trade_date"] for row in primary_rows}
    if filled:
        detail = "、".join(f"{name} {count} 处" for name, count in sorted(filled.items()))
        notes.append(f"备份源补齐：{detail}")
    for row in backup_rows:
        if row["trade_date"] in primary_dates:
            continue
        record = dict(row)
        record["quality_flag"] = "suspect"
        record["source"] = f"{row.get('source', 'backup')}(only)"
        merged.append(record)
        notes.append(f"{row['trade_date']} 仅备份源有数据，已按可疑写入")

    merged.sort(key=lambda r: r["trade_date"])
    # 只有真冲突才降级：notes 里既有"复权口径不同"这类说明，也有"仅备份源有数据"，
    # 后者已经逐行标了 suspect，不该再把整段序列判成可疑。
    flag = "suspect" if conflicts else "ok"
    if conflicts:
        notes.append(f"双源冲突 {len(conflicts)} 处")
    return MergeResult(rows=merged, quality_flag=flag, conflicts=conflicts, notes=notes)


def validate_bars(
    rows: Sequence[dict],
    cfg: dict,
    is_st: bool = False,
    is_new: bool = False,
    code: str = "",
    name: str | None = None,
    trading_days: dict | None = None,
) -> BarValidation:
    """涨跌幅跳变阻断；成交额异常只标记。

    "跳变"的判据不是固定的 11%，而是**那一类标的当天的法定涨跌幅**：
    创业板/科创板 20%、北交所 30%、主板 10%、主板 ST 5%，
    上市头几天（创业板科创板前 5 日、北交所首日）压根不设限。
    传 code / name / trading_days（{交易日: 上市第几天}）就会按阶段算；
    不传就退回配置里的 jump_pct_limit，行为和以前一样。
    """
    jump_limit = float(cfg["validation"].get("jump_pct_limit", 11.0))
    volume_ratio = float(cfg["validation"].get("volume_anomaly_ratio", 10.0))
    days_map = trading_days or {}

    checked: list[dict] = []
    blocked: list[dict] = []
    anomalies: list[dict] = []
    amounts: list[float] = []

    for row in rows:
        record = dict(row)
        pct = record.get("pct_chg")
        if code:
            limit = limits.max_move_pct(
                code, name, days_map.get(record.get("trade_date")), cfg,
                up=(pct is not None and float(pct) >= 0),
            )
        else:
            limit = jump_limit        # 调用方没给标的身份：维持老口径
        if pct is not None and not is_st and not is_new and abs(float(pct)) > limit:
            record["quality_flag"] = "blocked"
            blocked.append({
                "trade_date": record["trade_date"], "pct_chg": pct,
                "reason": f"涨跌幅超过当日上限 {limit:.0f}%",
            })
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
