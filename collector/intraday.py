"""分钟线聚合：把 1 分钟分时线聚成 5 / 30 / 60 分钟 K 线。

表 `bars_intraday` 的 `period` 字段本来就是为这个留的（主键是 code + dt + period），
所以聚合结果和原始 1 分钟数据放在同一张表里，按 period 区分，互不覆盖。

**口径（A 股约定）**：
  - 一个交易日 = 240 个"可交易分钟"：上午 09:30–11:29、下午 13:00–14:59；
  - 11:30 和 15:00 的收盘打印并进各自时段的最后一根，所以 30 分钟正好 8 根、
    5 分钟正好 48 根，和行情软件上看到的一致；
  - 每根 K 线的 dt 用它**区间起点**（09:30、10:00、13:00…），不是终点——
    终点制各家软件叫法不一，起点制在这里没有歧义，字段说明里也写明了。

聚合只做加减法（开高低收、成交量、成交额），不引入任何判断，所以它不会改变结论，
只是把数据换个粒度放好，供跨周期规则和分时图使用。
"""

from __future__ import annotations

from . import db

PERIODS = (5, 30, 60)
MORNING_OPEN = 9 * 60 + 30      # 09:30
MORNING_CLOSE = 11 * 60 + 30    # 11:30（并进上午最后一根）
AFTERNOON_OPEN = 13 * 60        # 13:00
AFTERNOON_CLOSE = 15 * 60       # 15:00（并进下午最后一根）
SESSION_MINUTES = 240           # 上午 120 + 下午 120


def _minute_of_day(dt: str) -> int | None:
    """'2026-09-17 09:31' → 571。解析不出来返回 None。"""
    text = str(dt or "")
    if len(text) < 16 or text[10] != " " or text[13] != ":":
        return None
    try:
        return int(text[11:13]) * 60 + int(text[14:16])
    except ValueError:
        return None


def session_index(dt: str) -> int | None:
    """当天的第几个可交易分钟（0–239）。不在交易时段内返回 None。

    午休和 11:30 / 15:00 的收盘打印都按上面 docstring 的口径折算，
    这样按 period 整除就能得到和行情软件一致的根数。
    """
    minute = _minute_of_day(dt)
    if minute is None:
        return None
    if MORNING_OPEN <= minute <= MORNING_CLOSE:
        return min(minute - MORNING_OPEN, 119)          # 11:30 并进第 120 分钟
    if AFTERNOON_OPEN <= minute <= AFTERNOON_CLOSE:
        return 120 + min(minute - AFTERNOON_OPEN, 119)  # 15:00 并进第 240 分钟
    return None


def aggregate_points(points: list[dict], period: int, source: str = "agg-1m") -> list[dict]:
    """把某只标的某一天的 1 分钟点聚成 period 分钟的 K 线。

    points 必须是同一天、按时间升序的 1 分钟数据；不足一个完整区间也算一根
    （盘中就该这样：先给出半根，收盘后自然补齐）。
    """
    if period <= 1:
        raise ValueError("period 必须大于 1 分钟；1 分钟本身就是原始数据")
    buckets: dict[int, dict] = {}
    order: list[int] = []
    for row in points:
        index = session_index(str(row.get("dt") or ""))
        if index is None:
            continue                                  # 集合竞价等时段外的点不参与
        slot = index // period
        price = row.get("close")
        if price is None:
            continue
        bar = buckets.get(slot)
        if bar is None:
            buckets[slot] = {
                "code": row["code"],
                "dt": row["dt"],                      # 区间起点：本桶第一个点的时间
                "period": period,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": float(row.get("volume") or 0.0),
                "amount": float(row.get("amount") or 0.0),
                "source": source,
            }
            order.append(slot)
            continue
        bar["high"] = max(bar["high"], price)
        bar["low"] = min(bar["low"], price)
        bar["close"] = price
        bar["volume"] += float(row.get("volume") or 0.0)
        bar["amount"] += float(row.get("amount") or 0.0)
    return [buckets[slot] for slot in sorted(order)]


def _day_points(conn, code: str, day: str) -> list[dict]:
    return [
        dict(row)
        for row in db.query(
            conn,
            """SELECT code, dt, close, volume, amount FROM bars_intraday
               WHERE code=? AND period=1 AND dt LIKE ? ORDER BY dt""",
            (code, f"{day}%"),
        )
    ]


def _pairs(conn, period: int, days: int | None, force_days: set | None) -> list:
    """要聚合的（标的 × 交易日）清单。

    force_days 给出时按它来（重算指定日期，用于 1 分钟数据刚被修正之后）；
    否则只挑还没有该周期聚合结果的，避免重复劳动。
    """
    sql = """SELECT DISTINCT b.code AS code, substr(b.dt, 1, 10) AS day
             FROM bars_intraday b WHERE b.period = 1"""
    params: list = []
    if force_days:
        placeholders = ",".join("?" * len(force_days))
        sql += f" AND substr(b.dt, 1, 10) IN ({placeholders})"
        params.extend(sorted(force_days))
    else:
        sql += """ AND NOT EXISTS (
                     SELECT 1 FROM bars_intraday a
                     WHERE a.code = b.code AND a.period = ?
                       AND substr(a.dt, 1, 10) = substr(b.dt, 1, 10)
                   )"""
        params.append(period)
    rows = list(db.query(conn, sql + " ORDER BY day DESC, code", tuple(params)))
    if days and not force_days:
        keep = {
            row["day"]
            for row in db.query(
                conn,
                "SELECT DISTINCT substr(dt, 1, 10) AS day FROM bars_intraday ORDER BY day DESC LIMIT ?",
                (int(days),),
            )
        }
        rows = [row for row in rows if row["day"] in keep]
    return rows


def aggregate_missing(conn, periods=PERIODS, days: int | None = None,
                      force_days: set | None = None, verbose: bool = False) -> dict:
    """把还没聚合过的（标的 × 交易日）补上，返回每个周期写了多少根。

    聚合结果按主键 upsert，所以对同一批数据反复跑是幂等的；要重算已经聚合过的日期，
    就把那些日期放进 force_days。
    """
    written: dict[int, int] = {}
    for period in periods:
        count = 0
        for row in _pairs(conn, period, days, force_days):
            bars = aggregate_points(_day_points(conn, row["code"], row["day"]), period)
            if bars:
                db.upsert_rows(conn, "bars_intraday", bars, ["code", "dt", "period"])
                count += len(bars)
        written[period] = count
        if verbose and count:
            print(f"      {period} 分钟：补了 {count} 根")
    return written


def counts(conn, day: str | None = None) -> dict[int, int]:
    """各周期现有多少根（报告和自检查看用）。"""
    sql = "SELECT period, COUNT(*) AS n FROM bars_intraday"
    params: tuple = ()
    if day:
        sql += " WHERE substr(dt, 1, 10) = ?"
        params = (day,)
    sql += " GROUP BY period ORDER BY period"
    return {row["period"]: row["n"] for row in db.query(conn, sql, params)}
