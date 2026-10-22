"""交易账本：开仓冻结假设、平仓复盘、组合在险。

这个模块补的是系统原来缺的那一半：alerts 记录系统当时说了什么，
trades 记录我实际做了什么、当时怎么想的。两者必须分开，否则复盘
就会变成判断系统对不对，而不是检验自己有没有照做。

三条硬规则：
  1. 开仓快照不可改。论点、失效条件、入场价、止损价、R 写入后冻结；
     平仓只能补结果，不能回头改判断。没有这条，后见之明偏差会把复盘变成辩护词。
  2. 风险按合并口径算。同题材的几只票不是分散——它们同涨同跌，
     所以组合在险按风险额相加，并在超过单题材上限时报警。
  3. 纪律与盈亏分开记。赚钱也可能违规，亏钱也可能按纪律做对了。
"""

from __future__ import annotations

import sqlite3
from datetime import date

from . import costs, db
from .sources.base import exchange_of, normalize_symbol

FRAMEWORKS = ("core", "satellite", "short")
FRAMEWORK_ALIASES = {
    "core": "core", "核心": "core", "指数": "core",
    "satellite": "satellite", "卫星": "satellite", "个股": "satellite",
    "short": "short", "短线": "short", "交易": "short",
}
DEFAULT_LIMITS = {
    "single_risk_pct": 1.0,
    "portfolio_risk_pct": 2.0,
    "single_position_pct": 10.0,
    "single_theme_risk_pct": 1.0,
}


class JournalError(ValueError):
    """给人看的错误：参数不合法、重复开仓、重复平仓。"""


def limits(cfg: dict | None = None) -> dict:
    raw = ((cfg or {}).get("journal") or {}).get("limits") or {}
    out = dict(DEFAULT_LIMITS)
    for key in DEFAULT_LIMITS:
        if raw.get(key) is not None:
            out[key] = float(raw[key])
    return out


def normalize_framework(value: str | None) -> str:
    key = (value or "short").strip().lower()
    if key in FRAMEWORK_ALIASES:
        return FRAMEWORK_ALIASES[key]
    raise JournalError(f"未知框架：{value}（可选 core / satellite / short）")


def _positive(value, field: str, required: bool = False):
    if value is None:
        if required:
            raise JournalError(f"缺少 {field}")
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise JournalError(f"{field} 必须是数字：{value}") from exc
    if number <= 0:
        raise JournalError(f"{field} 必须大于 0：{value}")
    return number


def _text(value, field: str, required: bool = False) -> str | None:
    text = value.strip() if isinstance(value, str) else value
    if required and not text:
        raise JournalError(f"缺少 {field}：写不出这一项，说明这笔还没想清楚")
    return text or None


def _risk_warnings(row: dict, cfg: dict | None) -> list[str]:
    conf = limits(cfg)
    warnings: list[str] = []
    risk_pct = row.get("risk_pct")
    pos_pct = row.get("position_pct")
    if risk_pct is not None and risk_pct > conf["single_risk_pct"]:
        warnings.append(f"单笔风险 {risk_pct:.2f}% > 上限 {conf['single_risk_pct']:.2f}%")
    if pos_pct is not None and pos_pct > conf["single_position_pct"]:
        warnings.append(f"单一持仓 {pos_pct:.2f}% > 上限 {conf['single_position_pct']:.2f}%")
    if not row.get("stop_price"):
        warnings.append("没有失效价：这笔仓位无法定义 R，也没法在纪律层检验")
    if row.get("reward_risk") is not None and row["reward_risk"] < 2.0:
        warnings.append(f"赔率 {row['reward_risk']:.2f}:1 低于 2:1 的底线")
    return warnings


def open_trade(conn, cfg: dict | None = None, **data) -> dict:
    """写入一笔开仓。返回 id 与当时的风险告警（告警不阻止记账）。"""
    framework = normalize_framework(data.get("framework"))
    raw_code = _text(data.get("code"), "code", required=True)
    code = exchange_of(raw_code) + normalize_symbol(raw_code)
    entry = _positive(data.get("entry_price"), "entry_price", required=True)
    stop = _positive(data.get("stop_price"), "stop_price")
    target = _positive(data.get("target_price"), "target_price")
    shares = _positive(data.get("shares"), "shares")
    equity = _positive(data.get("account_equity"), "account_equity")
    risk_amount = _positive(data.get("risk_amount"), "risk_amount")
    if stop is not None and stop >= entry:
        raise JournalError("做多的失效价必须低于入场价")

    # R 的优先级：显式金额 > 股数×止损距离。两者都没有就允许留空，
    # 但会告警——没有 R 的仓位只能记账，不能进组合在险。
    if risk_amount is None and shares is not None and stop is not None:
        risk_amount = shares * (entry - stop)
    risk_pct = (risk_amount / equity * 100) if (risk_amount and equity) else None
    position_pct = (shares * entry / equity * 100) if (shares and equity) else None
    reward_risk = ((target - entry) / (entry - stop)) if (target and stop and entry > stop) else None

    row = {
        "code": code,
        "name": _text(data.get("name"), "name"),
        "framework": framework,
        "theme": _text(data.get("theme"), "theme"),
        "opened_on": _text(data.get("opened_on"), "opened_on") or date.today().isoformat(),
        "entry_price": entry,
        "stop_price": stop,
        "target_price": target,
        "shares": shares,
        "risk_amount": risk_amount,
        "account_equity": equity,
        "risk_pct": round(risk_pct, 4) if risk_pct is not None else None,
        "reward_risk": round(reward_risk, 4) if reward_risk is not None else None,
        "position_pct": round(position_pct, 4) if position_pct is not None else None,
        "thesis": _text(data.get("thesis"), "thesis", required=True),
        "invalidation": _text(data.get("invalidation"), "invalidation", required=True),
        "nonconsensus": _text(data.get("nonconsensus"), "nonconsensus"),
        "exit_plan": _text(data.get("exit_plan"), "exit_plan"),
        "alert_id": data.get("alert_id"),
        "created_at": db.now_iso(),
        "updated_at": db.now_iso(),
    }
    warnings = _risk_warnings(row, cfg)
    try:
        with conn:
            cursor = conn.execute(
                """INSERT INTO trades (code, name, framework, theme, opened_on, entry_price,
                       stop_price, target_price, shares, risk_amount, account_equity,
                       risk_pct, reward_risk, position_pct, thesis, invalidation,
                       nonconsensus, exit_plan, alert_id, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (row["code"], row["name"], row["framework"], row["theme"], row["opened_on"],
                 row["entry_price"], row["stop_price"], row["target_price"], row["shares"],
                 row["risk_amount"], row["account_equity"], row["risk_pct"], row["reward_risk"],
                 row["position_pct"], row["thesis"], row["invalidation"], row["nonconsensus"],
                 row["exit_plan"], row["alert_id"], row["created_at"], row["updated_at"]),
            )
    except sqlite3.IntegrityError as exc:
        raise JournalError(
            f"{row['code']} 在 {row['opened_on']} 的 {row['framework']} 账本里已经有一笔；"
            "同一笔不要记两次，补仓请单独开一笔并写清理由"
        ) from exc
    trade = db.query_one(conn, "SELECT * FROM trades WHERE id=?", (cursor.lastrowid,))
    return {"id": cursor.lastrowid, "trade": dict(trade) if trade else row, "warnings": warnings}


def _implied_shares(row: dict) -> float | None:
    if row.get("shares"):
        return float(row["shares"])
    if row.get("risk_amount") and row.get("entry_price") and row.get("stop_price"):
        gap = float(row["entry_price"]) - float(row["stop_price"])
        if gap > 0:
            return float(row["risk_amount"]) / gap
    return None


def close_trade(conn, trade_id: int, exit_price, closed_on: str | None = None,
                discipline: bool | int | None = None, alpha_beta: str | None = None,
                note: str | None = None, cfg: dict | None = None) -> dict:
    """补平仓信息。只能补一次；开仓那几列一个字都不改。"""
    row = db.query_one(conn, "SELECT * FROM trades WHERE id=?", (trade_id,))
    if not row:
        raise JournalError(f"没有这笔账：id={trade_id}")
    row = dict(row)
    if row.get("closed_on"):
        raise JournalError(f"这笔已经平仓（{row['closed_on']}），如需更正请新增一条备注，不要改历史")
    price = _positive(exit_price, "exit_price", required=True)
    shares = _implied_shares(row)
    pnl = (price - float(row["entry_price"])) * shares if shares else None
    notional = float(row["entry_price"]) * shares if shares else None
    cost_amount = (notional * costs.round_trip_pct(cfg, notional) / 100) if notional else None
    pnl_net = (pnl - cost_amount) if (pnl is not None and cost_amount is not None) else pnl
    if discipline is None:
        conf = None                     # 没填就是"不知道"，不能默认成"按纪律"
    else:
        conf = {True: 1, False: 0, 1: 1, 0: 0}.get(discipline)
        if conf is None:
            raise JournalError("discipline 只能是 1/0 或 true/false")
    day = (closed_on or date.today().isoformat()).strip()
    if day < row["opened_on"]:
        raise JournalError("平仓日不能早于开仓日")
    with conn:
        conn.execute(
            """UPDATE trades SET closed_on=?, exit_price=?, pnl=?, cost_amount=?, pnl_net=?,
                   discipline=?,
                   alpha_beta=?, exit_note=?, updated_at=? WHERE id=? AND closed_on IS NULL""",
            (day, price, round(pnl, 2) if pnl is not None else None,
             round(cost_amount, 2) if cost_amount is not None else None,
             round(pnl_net, 2) if pnl_net is not None else None, conf,
             _text(alpha_beta, "alpha_beta"), _text(note, "note"), db.now_iso(), trade_id),
        )
    return dict(db.query_one(conn, "SELECT * FROM trades WHERE id=?", (trade_id,)))


def list_trades(conn, status: str = "open") -> list[dict]:
    where = {"open": "closed_on IS NULL", "closed": "closed_on IS NOT NULL"}.get(status)
    sql = "SELECT * FROM trades"
    if where:
        sql += f" WHERE {where}"
    sql += " ORDER BY opened_on DESC, id DESC"
    return [dict(row) for row in db.query(conn, sql)]


def portfolio_risk(conn, cfg: dict | None = None) -> dict:
    """组合级在险：所有未平仓头寸的 R 合计 + 同题材合并。"""
    conf = limits(cfg)
    open_rows = list_trades(conn, "open")
    total = 0.0
    missing = 0
    by_framework: dict[str, float] = {}
    by_theme: dict[str, float] = {}
    breaches: list[dict] = []
    for row in open_rows:
        risk = row.get("risk_pct")
        if risk is None:
            missing += 1
            continue
        risk = float(risk)
        total += risk
        by_framework[row["framework"]] = by_framework.get(row["framework"], 0.0) + risk
        theme = row.get("theme") or f"单票:{row['code']}"
        by_theme[theme] = by_theme.get(theme, 0.0) + risk
        if risk > conf["single_risk_pct"]:
            breaches.append({"kind": "single", "key": row["code"], "value": round(risk, 4),
                             "limit": conf["single_risk_pct"]})
    if total > conf["portfolio_risk_pct"]:
        breaches.append({"kind": "portfolio", "key": "合计", "value": round(total, 4),
                         "limit": conf["portfolio_risk_pct"]})
    for theme, value in by_theme.items():
        if value > conf["single_theme_risk_pct"]:
            breaches.append({"kind": "theme", "key": theme, "value": round(value, 4),
                             "limit": conf["single_theme_risk_pct"]})
    return {
        "open_count": len(open_rows),
        "total_risk_pct": round(total, 4),
        "missing_risk": missing,
        "by_framework": {key: round(value, 4) for key, value in by_framework.items()},
        "by_theme": {key: round(value, 4) for key, value in by_theme.items()},
        "limits": conf,
        "breaches": breaches,
    }


def closed_stats(conn) -> dict:
    rows = list_trades(conn, "closed")
    results = [
        float(row["pnl_net"] if row.get("pnl_net") is not None else row["pnl"])
        for row in rows if (row.get("pnl_net") is not None or row.get("pnl") is not None)
    ]
    wins = [value for value in results if value > 0]
    losses = [value for value in results if value < 0]
    return {
        "closed": len(rows),
        "n": len(results),
        "win_rate": round(len(wins) / len(results) * 100, 1) if results else None,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "expectancy": round(sum(results) / len(results), 2) if results else None,
        "violations": sum(1 for row in rows if row.get("discipline") == 0),
    }


TIERS = (5, 10, 15, 20)


def unlock(conn) -> dict:
    """证据解锁：额度跟着成绩走，不跟着信心走。

    口径：累计已平仓笔数 + 扣成本后的平均期望 > 0 + 违纪 ≤ 2。
    30 笔升一档、90 笔封顶。这是**累计口径**，比笔记里"再 30 笔"略宽；
    等账本样本够了，再改成滚动窗口，别现在就假装精确。
    """
    stats = closed_stats(conn)
    n = stats["n"]
    healthy = (stats["expectancy"] or 0) > 0 and stats["violations"] <= 2
    if n >= 90 and healthy:
        tier = 20
    elif n >= 60 and healthy:
        tier = 15
    elif n >= 30 and healthy:
        tier = 10
    else:
        tier = 5
    next_at = next((step for step in (30, 60, 90) if n < step), None)
    return {
        "tier": tier,
        "n": n,
        "expectancy": stats["expectancy"],
        "violations": stats["violations"],
        "healthy": healthy,
        "next_at": next_at,
    }


def guardrails(conn) -> dict:
    """账户侧熔断：单月亏损、连续亏损、顺风冷却。

    只在账本有平仓记录时算；没有记录返回空告警，不假装"风控已通过"。
    """
    rows = [row for row in list_trades(conn, "closed") if row.get("closed_on")]
    rows.sort(key=lambda row: (row["closed_on"], row["id"]))
    if not rows:
        return {"flags": [], "month_pnl": None, "week_pnl": None, "consecutive_losses": 0}

    def net(row):
        return float(row["pnl_net"] if row.get("pnl_net") is not None else (row.get("pnl") or 0))

    equity = next((float(row["account_equity"]) for row in reversed(rows)
                   if row.get("account_equity")), None)
    # "本月/本周"按**今天**算，不按最后一笔交易算——否则三周前的顺风周
    # 会在今天继续触发冷却，等于把历史当成现在。
    today = date.today()
    month = today.isoformat()[:7]
    month_pnl = sum(net(row) for row in rows if row["closed_on"][:7] == month)
    month_pct = (month_pnl / equity * 100) if equity else None

    def week_key(day: str) -> tuple[int, int]:
        try:
            parsed = date.fromisoformat(day)
        except ValueError:
            return (0, 0)
        iso = parsed.isocalendar()
        return (iso.year, iso.week)

    weeks: dict[tuple[int, int], float] = {}
    for row in rows:
        key = week_key(row["closed_on"])
        weeks[key] = weeks.get(key, 0.0) + net(row)
    current_week = week_key(today.isoformat())

    def week_start(key: tuple[int, int]) -> date:
        try:
            return date.fromisocalendar(key[0], key[1], 1)
        except ValueError:
            return date.min

    recent_weeks = [key for key in sorted(weeks)
                    if 0 <= (today - week_start(key)).days <= 13]
    week_pnl = weeks.get(current_week, 0.0) if weeks else None
    week_pct = (week_pnl / equity * 100) if (week_pnl is not None and equity) else None

    consecutive = 0
    for row in reversed(rows):
        if net(row) < 0:
            consecutive += 1
        else:
            break

    flags = []
    if month_pct is not None and month_pct <= -3.0:
        flags.append(f"本月净亏损 {month_pct:.2f}% ≤ −3% → 当月停止新开仓")
    if consecutive >= 3:
        flags.append(f"连续 {consecutive} 笔亏损 → 降到半仓，重新检验信号")
    if week_pct is not None and week_pct >= 1.5:
        flags.append(f"本周净盈利 {week_pct:.2f}% ≥ +1.5% → 下周新开仓 ≤1 笔")
    elif len(recent_weeks) >= 2 and all(weeks[key] > 0 for key in recent_weeks[-2:]):
        flags.append("连续两周为正 → 下周新开仓 ≤1 笔")
    return {
        "flags": flags, "month_pnl": round(month_pnl, 2),
        "month_pct": round(month_pct, 2) if month_pct is not None else None,
        "week_pnl": round(week_pnl, 2) if week_pnl is not None else None,
        "week_pct": round(week_pct, 2) if week_pct is not None else None,
        "consecutive_losses": consecutive,
        "equity": equity,
    }


def report(conn, cfg: dict | None = None) -> str:
    risk = portfolio_risk(conn, cfg)
    conf = risk["limits"]
    rows = list_trades(conn, "open")
    lines = ["===== 交易账本 ====="]
    if not rows:
        lines.append("没有未平仓记录。开仓：python run.py journal open ...")
    else:
        lines.append(f"{'id':>4}  {'代码':<10}{'框架':<10}{'题材':<12}{'入场':>9}"
                     f"{'失效':>9}{'R%':>7}{'仓位%':>7}{'赔率':>7}")
        for row in rows:
            rr = f"{row['reward_risk']:.2f}" if row.get("reward_risk") is not None else "—"
            risk_pct = f"{row['risk_pct']:.2f}" if row.get("risk_pct") is not None else "—"
            pos = f"{row['position_pct']:.1f}" if row.get("position_pct") is not None else "—"
            lines.append(
                f"{row['id']:>4}  {row['code']:<10}{row['framework']:<10}"
                f"{(row.get('theme') or '—')[:12]:<12}{row['entry_price']:>9.3f}"
                f"{(row.get('stop_price') or 0):>9.3f}{risk_pct:>7}{pos:>7}{rr:>7}"
            )
    lines.append("")
    lines.append(f"组合在险：{risk['total_risk_pct']:.2f}% / 上限 {conf['portfolio_risk_pct']:.2f}%"
                 f"（{risk['open_count']} 笔未平仓）")
    if risk["by_framework"]:
        lines.append("  分框架：" + "，".join(
            f"{key} {value:.2f}%" for key, value in risk["by_framework"].items()))
    if risk["by_theme"]:
        lines.append("  分题材：" + "，".join(
            f"{key} {value:.2f}%" for key, value in risk["by_theme"].items()))
    if risk["missing_risk"]:
        lines.append(f"  ⚠ {risk['missing_risk']} 笔没填总资产/风险额，没进合计——"
                     "在险上限只能按已填的算")
    for item in risk["breaches"]:
        lines.append(f"  ✗ {item['kind']} {item['key']}：{item['value']:.2f}% > {item['limit']:.2f}%")
    stats = closed_stats(conn)
    if stats["closed"]:
        lines.append("")
        lines.append(f"已平仓 {stats['closed']} 笔：胜率 {stats['win_rate']}%，"
                     f"扣成本期望 {stats['expectancy']} 元/笔，违纪 {stats['violations']} 次")
        tier = unlock(conn)
        lines.append(f"额度档位：{tier['tier']}%"
                     + (f"（再 {tier['next_at'] - tier['n']} 笔进入下一档）"
                        if tier["next_at"] else "（已封顶）")
                     + ("" if tier["healthy"] else "；当前不满足升档条件"))
        guard = guardrails(conn)
        if guard["flags"]:
            lines.append("熔断／冷却：")
            for flag in guard["flags"]:
                lines.append(f"  ! {flag}")
        else:
            lines.append("熔断／冷却：未触发")
    return "\n".join(lines)
