"""信号复盘：回填提醒的真实表现、统计胜率、复查仲裁判得对不对。

口径和项目其它地方保持一致，避免自欺：
  - 信号在某日收盘确认
  - 建仓 = 次一交易日收盘（A 股 T+1，不使用未来数据）
  - N 日表现 = 建仓后第 N 个交易日的收盘 ÷ 建仓价 − 1

这套数字是判断"哪条规则有用、哪个因子该退役"的唯一依据（规格 §13）。
"""

from __future__ import annotations

import bisect
from typing import Iterable

from . import db

HORIZONS = (5, 20)


def _series(conn, code: str, cache: dict) -> tuple[list[str], list[dict]]:
    if code not in cache:
        rows = db.query(conn, "SELECT trade_date, close FROM bars_daily WHERE code=? ORDER BY trade_date", (code,))
        cache[code] = ([row["trade_date"] for row in rows], [dict(row) for row in rows])
    return cache[code]


def forward_return(bars: list[dict], dates: list[str], alert_date: str, horizon: int) -> float | None:
    """信号日之后第 horizon 个交易日的收益（%）。数据不够就返回 None。"""
    entry = bisect.bisect_right(dates, alert_date)          # 次日建仓
    exit_pos = entry + horizon                              # 建仓后第 horizon 个交易日
    if entry >= len(dates) or exit_pos >= len(dates):
        return None
    entry_close = bars[entry].get("close")
    exit_close = bars[exit_pos].get("close")
    if not entry_close or not exit_close:
        return None
    return round((exit_close / entry_close - 1) * 100, 4)


def backfill_outcomes(conn, verbose: bool = False) -> dict:
    """把提醒和被压制信号的实际表现补进库里。已经填过且数据没变的不会重复写。"""
    cache: dict = {}
    updated_alerts = 0
    updated_conflicts = 0

    for row in db.query(conn, "SELECT id, code, trade_date, outcome_5d, outcome_20d FROM alerts"):
        dates, bars = _series(conn, row["code"], cache)
        sets: list[str] = []
        params: list = []
        for horizon, column in zip(HORIZONS, ("outcome_5d", "outcome_20d")):
            value = forward_return(bars, dates, row["trade_date"], horizon)
            if value is not None and row[column] != value:
                sets.append(f"{column}=?")
                params.append(value)
        if sets:
            params.append(row["id"])
            conn.execute(f"UPDATE alerts SET {', '.join(sets)} WHERE id=?", params)
            updated_alerts += 1

    for row in db.query(
        conn, "SELECT id, code, trade_date, outcome_20d FROM arbitration_log WHERE outcome_20d IS NULL"
    ):
        dates, bars = _series(conn, row["code"], cache)
        value = forward_return(bars, dates, row["trade_date"], 20)
        if value is not None:
            conn.execute("UPDATE arbitration_log SET outcome_20d=? WHERE id=?", (value, row["id"]))
            updated_conflicts += 1

    conn.commit()
    if verbose and (updated_alerts or updated_conflicts):
        print(f"      回填：提醒 {updated_alerts} 条、被压制信号 {updated_conflicts} 条")
    return {"alerts": updated_alerts, "conflicts": updated_conflicts}


def signal_stats(conn) -> list[dict]:
    """按级别 / 信号类型统计胜率。done 是已经能算出结果的条数。"""
    return [
        dict(row)
        for row in db.query(
            conn,
            """
            SELECT level,
                   signal_type,
                   COUNT(*)                                        AS n,
                   SUM(CASE WHEN outcome_5d IS NOT NULL THEN 1 ELSE 0 END)  AS done5,
                   SUM(CASE WHEN outcome_5d > 0 THEN 1 ELSE 0 END)          AS win5,
                   ROUND(AVG(outcome_5d), 2)                       AS avg5,
                   SUM(CASE WHEN outcome_20d IS NOT NULL THEN 1 ELSE 0 END) AS done20,
                   SUM(CASE WHEN outcome_20d > 0 THEN 1 ELSE 0 END)         AS win20,
                   ROUND(AVG(outcome_20d), 2)                      AS avg20
            FROM alerts
            GROUP BY level, signal_type
            ORDER BY level, n DESC
            """,
        )
    ]


def conflict_stats(conn) -> list[dict]:
    """仲裁复盘：被压制的信号后来跌了，说明压制是对的（买入方向信号）。"""
    return [
        dict(row)
        for row in db.query(
            conn,
            """
            SELECT rule_applied,
                   COUNT(*)                                       AS n,
                   SUM(CASE WHEN outcome_20d IS NOT NULL THEN 1 ELSE 0 END) AS done,
                   SUM(CASE WHEN outcome_20d < 0 THEN 1 ELSE 0 END)         AS suppressed_ok,
                   ROUND(AVG(outcome_20d), 2)                     AS avg20
            FROM arbitration_log
            GROUP BY rule_applied
            ORDER BY n DESC
            """,
        )
    ]


def report(conn, stats: Iterable[dict] | None = None, conflicts: Iterable[dict] | None = None) -> str:
    """人看的复盘报告。样本小的时候会明说，不让人误读。"""
    stats = list(stats if stats is not None else signal_stats(conn))
    conflicts = list(conflicts if conflicts is not None else conflict_stats(conn))
    lines = ["===== 信号复盘 ====="]

    if not stats:
        lines.append("还没有提醒记录。")
    else:
        lines.append("")
        lines.append("按级别 / 信号类型")
        lines.append(f"{'级别':<4}{'信号':<22}{'条数':>5}{'5日胜率':>9}{'5日均值':>9}{'20日胜率':>9}{'20日均值':>9}")
        for row in stats:
            win5 = f"{row['win5'] / row['done5'] * 100:.0f}%" if row["done5"] else "—"
            win20 = f"{row['win20'] / row['done20'] * 100:.0f}%" if row["done20"] else "—"
            avg5 = f"{row['avg5']:+.2f}%" if row["avg5"] is not None else "—"
            avg20 = f"{row['avg20']:+.2f}%" if row["avg20"] is not None else "—"
            lines.append(
                f"{row['level']:<4}{row['signal_type']:<22}{row['n']:>5}{win5:>9}{avg5:>9}{win20:>9}{avg20:>9}"
            )

    if conflicts:
        lines.append("")
        lines.append("仲裁复盘（被压制的买入信号后来跌了 = 压得对）")
        for row in conflicts:
            right = f"{row['suppressed_ok']}/{row['done']}" if row["done"] else "—"
            avg = f"{row['avg20']:+.2f}%" if row["avg20"] is not None else "—"
            lines.append(f"  {row['rule_applied']:<20} 压制 {row['n']:>3} 次，20 日后下跌 {right}，平均 {avg}")

    pending = sum(row["n"] - row["done20"] for row in stats)
    if pending:
        lines.append("")
        lines.append(f"（还有 {pending} 条信号不满 20 个交易日，等数据长出来再回填）")
    lines.append("")
    lines.append("提醒：观察池小、样本少的时候胜率波动很大，别用几个月的数据做结论。")
    return "\n".join(lines)
