"""交易时段判断。实时数据只在交易时段有意义，非交易时段就别装作在刷新。

口径：A 股 09:30–11:30、13:00–15:00（含两端），并且那天得是交易日
（优先查 trade_calendar，查不到就退回"工作日"判断——节假日表是逐步补的）。
"""

from __future__ import annotations

from datetime import datetime

from . import db

MORNING = (9 * 60 + 30, 11 * 60 + 30)
AFTERNOON = (13 * 60, 15 * 60)


def _minutes(when: datetime) -> int:
    return when.hour * 60 + when.minute


def in_session(when: datetime | None = None) -> bool:
    """只看钟点：此刻是否在交易时段内。"""
    moment = when or datetime.now()
    if moment.weekday() >= 5:
        return False
    minute = _minutes(moment)
    return MORNING[0] <= minute <= MORNING[1] or AFTERNOON[0] <= minute <= AFTERNOON[1]


def is_trading_day(conn, day: str) -> bool:
    row = db.query_one(conn, "SELECT is_trading_day FROM trade_calendar WHERE trade_date=?", (day,))
    if row is None:
        return datetime.strptime(day, "%Y-%m-%d").weekday() < 5
    return bool(row["is_trading_day"])


def is_trading_now(conn, when: datetime | None = None) -> bool:
    """两个条件都满足才算"正在交易"：今天是交易日 + 此刻在时段内。"""
    moment = when or datetime.now()
    if not in_session(moment):
        return False
    return is_trading_day(conn, moment.strftime("%Y-%m-%d"))


def describe(conn, when: datetime | None = None) -> str:
    """给人看的一句话状态：盘中 / 午休 / 已收盘 / 非交易日。"""
    moment = when or datetime.now()
    day = moment.strftime("%Y-%m-%d")
    if not is_trading_day(conn, day):
        return "非交易日"
    minute = _minutes(moment)
    if MORNING[0] <= minute <= MORNING[1]:
        return "交易中"
    if MORNING[1] < minute < AFTERNOON[0]:
        return "午休"
    if AFTERNOON[0] <= minute <= AFTERNOON[1]:
        return "交易中"
    return "已收盘" if minute > AFTERNOON[1] else "未开盘"
