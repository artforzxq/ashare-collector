"""信号复盘：回填提醒的真实表现、统计胜率、复查仲裁判得对不对。

口径和项目其它地方保持一致，避免自欺：
  - 信号在某日收盘确认
  - 建仓 = 次一交易日收盘（A 股 T+1，不使用未来数据）
  - N 日表现 = 建仓后第 N 个交易日的收盘 ÷ 建仓价 − 1

这套数字是判断"哪条规则有用、哪个因子该退役"的唯一依据（规格 §13）。
"""

from __future__ import annotations

import bisect
import statistics
from typing import Iterable

from . import costs, db

HORIZONS = (5, 20)
# 提醒复盘的对照组：沪深300。不是"随便买"的全部答案，但至少把"大盘涨了"这件事先扣掉。
BENCHMARK_CODE = "SH000300"


def load_series(conn, code: str, cache: dict) -> tuple[list[str], list[dict]]:
    """某只标的的日期序列和日线（带缓存）。筛选回填也用它，口径必须一致。"""
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
        dates, bars = load_series(conn, row["code"], cache)
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
        dates, bars = load_series(conn, row["code"], cache)
        value = forward_return(bars, dates, row["trade_date"], 20)
        if value is not None:
            conn.execute("UPDATE arbitration_log SET outcome_20d=? WHERE id=?", (value, row["id"]))
            updated_conflicts += 1

    conn.commit()
    if verbose and (updated_alerts or updated_conflicts):
        print(f"      回填：提醒 {updated_alerts} 条、被压制信号 {updated_conflicts} 条")
    return {"alerts": updated_alerts, "conflicts": updated_conflicts}


def signal_stats(conn, cost: float = 0.0) -> list[dict]:
    """按级别 / 信号类型统计胜率。done 是已经能算出结果的条数。

    `cost` 传往返成本（%）。毛/净两列同时给：毛收益回答"价格后来怎么走"，
    净收益回答"我能不能赚到"——后者才是决定要不要继续做这条规则的依据。
    胜率也必须用净收益算：一个 +0.1% 的"涨"扣掉 0.302% 成本后是亏的。
    """
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
                   SUM(CASE WHEN outcome_5d - ? > 0 THEN 1 ELSE 0 END)      AS win5_net,
                   ROUND(AVG(outcome_5d), 2)                       AS avg5,
                   ROUND(AVG(outcome_5d - ?), 2)                   AS avg5_net,
                   SUM(CASE WHEN outcome_20d IS NOT NULL THEN 1 ELSE 0 END) AS done20,
                   SUM(CASE WHEN outcome_20d > 0 THEN 1 ELSE 0 END)         AS win20,
                   SUM(CASE WHEN outcome_20d - ? > 0 THEN 1 ELSE 0 END)     AS win20_net,
                   ROUND(AVG(outcome_20d), 2)                      AS avg20,
                   ROUND(AVG(outcome_20d - ?), 2)                  AS avg20_net
            FROM alerts
            GROUP BY level, signal_type
            ORDER BY level, n DESC
            """,
            (cost, cost, cost, cost),
        )
    ]


def benchmark_stats(conn, cost: float = 0.0) -> list[dict]:
    """每条提醒扣成本后，和同期沪深300 比一比。

    笔记里那句"短线的对照组不是零，而是定投核心"在这里的落地版本：
    绝对收益 +2% 但同期沪深300 +4%，这条信号依然没有价值。
    基准取不到（本地没有 SH000300 日线）时返回空表，报告里明说缺数据，
    不拿 0 冒充"没有跑输"。
    """
    series, bars = load_series(conn, BENCHMARK_CODE, {})
    if not series:
        return []
    rows = db.query(
        conn,
        """SELECT signal_type, trade_date, outcome_20d FROM alerts
           WHERE outcome_20d IS NOT NULL ORDER BY signal_type""",
    )
    grouped: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        bench = forward_return(bars, series, row["trade_date"], 20)
        if bench is None:
            continue
        slot = grouped.setdefault(row["signal_type"], {"net": [], "excess": []})
        net = float(row["outcome_20d"]) - cost
        # 基准也走同一笔往返：多扣一次成本会把"摩擦差"当成"信号失效"。
        bench_net = bench - cost
        slot["net"].append(net)
        slot["excess"].append(net - bench_net)
    out = []
    for signal_type, values in grouped.items():
        out.append({
            "signal_type": signal_type,
            "n": len(values["net"]),
            "net20": round(statistics.mean(values["net"]), 2),
            "excess20": round(statistics.mean(values["excess"]), 2),
            "win20_net": sum(1 for value in values["net"] if value > 0),
        })
    out.sort(key=lambda item: item["excess20"])
    return out


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


def report(conn, stats: Iterable[dict] | None = None, conflicts: Iterable[dict] | None = None,
           cfg: dict | None = None) -> str:
    """人看的复盘报告。样本小的时候会明说，不让人误读。"""
    cost = costs.round_trip_pct(cfg) if cfg is not None else 0.0
    stats = list(stats if stats is not None else signal_stats(conn, cost))
    conflicts = list(conflicts if conflicts is not None else conflict_stats(conn))
    lines = ["===== 信号复盘 =====", costs.describe(cfg) if cfg is not None
             else "成本口径：未传配置（毛收益）"]

    if not stats:
        lines.append("还没有提醒记录。")
    else:
        lines.append("")
        lines.append("按级别 / 信号类型")
        lines.append(f"{'级别':<4}{'信号':<22}{'条数':>5}{'5日胜率':>9}{'5日净':>9}"
                     f"{'20日胜率':>9}{'20日净':>9}{'扣成本胜率':>10}")
        for row in stats:
            win5 = f"{row['win5'] / row['done5'] * 100:.0f}%" if row["done5"] else "—"
            win20 = f"{row['win20'] / row['done20'] * 100:.0f}%" if row["done20"] else "—"
            win20_net = (f"{row['win20_net'] / row['done20'] * 100:.0f}%"
                         if row["done20"] else "—")
            avg5 = f"{row['avg5_net']:+.2f}%" if row["avg5_net"] is not None else "—"
            avg20 = f"{row['avg20_net']:+.2f}%" if row["avg20_net"] is not None else "—"
            lines.append(
                f"{row['level']:<4}{row['signal_type']:<22}{row['n']:>5}{win5:>9}{avg5:>9}"
                f"{win20:>9}{avg20:>9}{win20_net:>10}"
            )

        benched = benchmark_stats(conn, cost)
        if benched:
            lines.append("")
            lines.append(f"扣成本后 vs 同期沪深300（20 日，成本 {cost:.3f}%）")
            lines.append(f"  {'信号':<22}{'样本':>6}{'净收益':>9}{'超额':>9}{'净胜率':>8}")
            for row in benched:
                win = f"{row['win20_net'] / row['n'] * 100:.0f}%" if row["n"] else "—"
                lines.append(f"  {row['signal_type']:<22}{row['n']:>6}"
                             f"{row['net20']:>+8.2f}%{row['excess20']:>+8.2f}%{win:>8}")
        else:
            lines.append("")
            has_bench = db.query_one(
                conn, "SELECT 1 AS ok FROM bars_daily WHERE code=? LIMIT 1", (BENCHMARK_CODE,))
            if has_bench:
                lines.append("（提醒都还不满 20 个交易日，暂时没有沪深300 对照组；"
                             "等数据长出来会自动出现）")
            else:
                lines.append("（本地缺 SH000300 日线，提醒复盘暂时没有沪深300 对照组）")

        # 胜率校准建议：只在 P1（可操作）样本足够时给，避免用几条记录改全局参数。
        p1_done = sum(int(row["done20"] or 0) for row in stats if row["level"] == "P1")
        p1_wins = sum(int(row["win20_net"] or 0) for row in stats if row["level"] == "P1")
        if p1_done >= 30:
            rate = p1_wins / p1_done
            lines.append("")
            lines.append(f"校准建议：P1 提醒 20 日净胜率 {rate:.0%}（样本 {p1_done} 条，已扣 "
                         f"{cost:.3f}% 成本）——可据此调整 config.yaml 的 risk.win_rate；"
                         "改完记得升 project.feature_version。")

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
