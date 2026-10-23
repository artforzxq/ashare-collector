"""成交额比较：只做展示口径，不参与市场层打分。

为什么要单独一个模块：项目里已经有 `flow.amount_ratio`（60 日中位数）和
`regime.amount_window`（市场层用的 60 日）。页面如果再自己算一套，就会出现
三个"放量倍数"。这里统一成：昨日、5 日均量、N 日中位数/分位，并且先检查
今天和基期的**覆盖股票数是否可比**。

关键约束：成交额是"全市场个股成交额"，不是所有 bars 的金额之和。
市场广度某天只覆盖 171 只时，那个数字不能拿来和 5500 只的一天比。
"""

from __future__ import annotations

import statistics

from . import db

DEFAULTS = {
    "windows": (1, 3, 5, 10, 20, 60),
    "default": 20,
    "min_coverage": 1000,
    "max_coverage_gap_pct": 5.0,
}


def settings(cfg: dict | None = None) -> dict:
    raw = ((cfg or {}).get("market") or {}).get("amount_compare") or {}
    return {
        "windows": tuple(int(value) for value in raw.get("windows") or DEFAULTS["windows"]),
        "default": int(raw.get("default", DEFAULTS["default"])),
        "min_coverage": int(raw.get("min_coverage", DEFAULTS["min_coverage"])),
        "max_coverage_gap_pct": float(raw.get("max_coverage_gap_pct", DEFAULTS["max_coverage_gap_pct"])),
    }


def _label(percentile: float | None) -> str:
    if percentile is None:
        return "样本不足"
    if percentile < 20:
        return "显著缩量"
    if percentile < 40:
        return "偏缩"
    if percentile <= 60:
        return "常态"
    if percentile <= 80:
        return "偏放"
    return "显著放量"


def _window(history: list[dict], window: int, today: float, coverage: int,
            conf: dict) -> dict:
    base = history[:window]
    if len(base) < window:
        return {"days": window, "available": len(base), "comparable": False,
                "reason": f"只有 {len(base)} 个基期交易日，至少需要 {window} 个"}
    amounts = [float(row["total_amount"]) for row in base if row.get("total_amount")]
    coverages = [int(row.get("coverage") or 0) for row in base]
    if len(amounts) < window:
        return {"days": window, "available": len(amounts), "comparable": False,
                "reason": "基期有个别交易日没有成交额"}
    if coverage < conf["min_coverage"]:
        return {"days": window, "comparable": False,
                "reason": f"今日覆盖 {coverage} 只，低于 {conf['min_coverage']}"}
    if min(coverages) < conf["min_coverage"]:
        return {"days": window, "comparable": False,
                "reason": f"基期覆盖最少 {min(coverages)} 只，低于 {conf['min_coverage']}"}
    baseline_cov = statistics.median(coverages)
    gap = abs(coverage - baseline_cov) / max(baseline_cov, 1) * 100
    if gap > conf["max_coverage_gap_pct"]:
        return {"days": window, "comparable": False, "coverage_gap_pct": round(gap, 2),
                "reason": f"今日与基期覆盖差 {gap:.2f}%，超过 {conf['max_coverage_gap_pct']:.0f}%"}
    spread = (max(coverages) - min(coverages)) / max(baseline_cov, 1) * 100
    if spread > conf["max_coverage_gap_pct"]:
        return {"days": window, "comparable": False, "coverage_spread_pct": round(spread, 2),
                "reason": f"基期覆盖波动 {spread:.2f}%，超过 {conf['max_coverage_gap_pct']:.0f}%"}
    if window == 1:
        baseline = amounts[0]
        basis = "前一交易日"
    elif window == 5:
        baseline = statistics.mean(amounts)
        basis = "5日均量"
    else:
        baseline = statistics.median(amounts)
        basis = "中位数"
    if baseline <= 0:
        return {"days": window, "comparable": False, "reason": "基期成交额为 0"}
    ratio = today / baseline
    percentile = sum(1 for value in amounts if value <= today) / len(amounts) * 100
    label = (
        ("显著放量" if ratio >= 1.3 else "偏放" if ratio >= 1.1 else
         "显著缩量" if ratio <= 0.7 else "偏缩" if ratio <= 0.9 else "常态")
        if window == 1 else _label(percentile)
    )
    return {
        "days": window,
        "basis": basis,
        "baseline": round(baseline, 0),
        "ratio": round(ratio, 3),
        "change_pct": round((ratio - 1) * 100, 2),
        "percentile": None if window == 1 else round(percentile, 1),
        "label": label,
        "comparable": True,
        "coverage_gap_pct": round(gap, 2),
    }


def compute(conn, cfg: dict | None, trade_date: str, today: float,
            coverage: int) -> dict:
    conf = settings(cfg)
    max_window = max(conf["windows"])
    rows = [
        dict(row)
        for row in db.query(
            conn,
            """SELECT trade_date, total_amount, coverage
                 FROM market_breadth WHERE trade_date<?
                ORDER BY trade_date DESC LIMIT ?""",
            (trade_date, max_window),
        )
    ]
    result = {
        "as_of": trade_date,
        "value": round(today, 0),
        "coverage": coverage,
        "min_coverage": conf["min_coverage"],
        "max_coverage_gap_pct": conf["max_coverage_gap_pct"],
        "default_window": conf["default"],
        "windows": {},
        "history": [],
    }
    for window in conf["windows"]:
        result["windows"][str(window)] = _window(rows, window, today, coverage, conf)

    # 昨日单独给出来：它是用户最直观的"相比昨天"，不和其它窗口混。
    if rows:
        prev = rows[0]
        result.update({
            "prev_date": prev.get("trade_date"),
            "prev_value": prev.get("total_amount"),
            "prev_coverage": prev.get("coverage"),
        })
        if prev.get("total_amount"):
            ratio = today / float(prev["total_amount"])
            one = result["windows"].get("1") or {}
            result["d1"] = {
                "ratio": round(ratio, 3),
                "change_pct": round((ratio - 1) * 100, 2),
                "comparable": one.get("comparable"),
                "label": one.get("label") or (
                    "显著放量" if ratio >= 1.3 else
                    "偏放" if ratio >= 1.1 else
                    "显著缩量" if ratio <= 0.7 else
                    "偏缩" if ratio <= 0.9 else
                    "常态"
                ),
            }
    history = list(reversed(rows))
    history.append({"trade_date": trade_date, "total_amount": today, "coverage": coverage})
    result["history"] = [
        {"trade_date": row["trade_date"],
         "total_amount": round(float(row["total_amount"] or 0), 0),
         "coverage": row.get("coverage")}
        for row in history
    ]
    return result
