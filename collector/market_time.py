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


# ---------------------------------------------------------------- 上市阶段
#
# 新股的涨跌停规则和"上市第几个交易日"绑在一起（创业板科创板前 5 日不设限、
# 北交所首日不设限、主板首日 44%/36%），所以日终校验必须先知道这个天数，
# 否则会把合法的暴涨当成脏数据（这正是以前"跳变保护"误伤新股的原因）。

def listed_date_of(conn, code: str) -> str | None:
    """上市日：权威的优先，退而求其次用新股表里推算的，再不行就本地日线第一根。"""
    for sql in ("SELECT listed_date FROM instruments WHERE code=?",
                "SELECT listed_date FROM new_listings WHERE code=?"):
        try:
            row = db.query_one(conn, sql, (code,))
        except Exception:
            row = None
        if row and row["listed_date"]:
            return row["listed_date"]
    row = db.query_one(conn, "SELECT MIN(trade_date) AS d FROM bars_daily WHERE code=?", (code,))
    return row["d"] if row and row["d"] else None


def trading_days_between(conn, listed_date: str | None, trade_date: str) -> int | None:
    """上市第几个交易日（首日 = 1）。算不出来就返回 None（按"已过观察期"处理）。"""
    if not listed_date or not trade_date:
        return None
    row = db.query_one(
        conn,
        """SELECT COUNT(*) AS n FROM trade_calendar
           WHERE is_trading_day=1 AND trade_date>=? AND trade_date<=?""",
        (listed_date, trade_date),
    )
    if row and row["n"]:
        return max(1, int(row["n"]))
    try:
        span = (datetime.strptime(trade_date, "%Y-%m-%d")
                - datetime.strptime(listed_date, "%Y-%m-%d")).days
    except ValueError:
        return None
    return max(1, int(span * 0.69))      # 没有交易日历时按日历日折算（一周约 5 个交易日）


def trading_days_map(conn, trade_date: str) -> dict[str, int]:
    """{标的: 上市第几个交易日}：一次 SQL 算全市场，够市场广度这种批量场景用。"""
    try:
        rows = db.query(
            conn,
            """SELECT i.code AS code,
                      (SELECT COUNT(*) FROM trade_calendar t
                        WHERE t.is_trading_day=1 AND t.trade_date>=i.listed_date
                          AND t.trade_date<=?) AS days
               FROM instruments i WHERE i.listed_date IS NOT NULL""",
            (trade_date,),
        )
    except Exception:
        return {}
    return {row["code"]: int(row["days"]) for row in rows if row["days"]}
