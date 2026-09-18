"""市场广度：涨跌家数、涨跌停家数、中位数涨跌幅、成交额。

两条来源、同一套算法：
  1. 数据源的全市场快照——一次请求覆盖全市场，最全，但那条接口在本机网络上
     经常连不上（DNS 都解析不了）；
  2. **本地仓库**——把当天已入库的 K 线自己数一遍。本地有 5500 多只票时，
     这就是真实的全市场广度，而且不依赖任何网络。

本地这条路是主力：市场广度不该因为"今天接口又断了"而永远空着。
涨跌停的判定复用 collector/limits.py，和回测、风险层用同一套口径
（主板 10%、创业板科创板 20%、北交所 30%、ST 5%），比"涨跌幅 ≥9.8% 就算涨停"
那种一刀切准得多。
"""

from __future__ import annotations

import statistics

from . import db, limits

# 少于这么多只票就不该叫"市场广度"，宁可空着也别给个数。
# 全市场个股约 5500 只；本地仓库历史日期都能到 5500+，只有当天刚开盘才会很小。
DEFAULT_MIN_COVERAGE = 1000


def min_coverage(cfg: dict | None) -> int:
    """配置里的样本下限。缺失用默认值，显式写 0 表示不设下限。"""
    section = (cfg or {}).get("breadth") or {}
    value = section.get("min_coverage")
    return DEFAULT_MIN_COVERAGE if value is None else int(value)


def _pct(row: dict) -> float | None:
    """当日涨跌幅：优先用库里那列；缺失时用收盘价和前收自己算（能救回 0.3% 的缺口）。"""
    value = row.get("pct_chg")
    if value is not None:
        return float(value)
    close, pre_close = row.get("close"), row.get("pre_close")
    if close and pre_close:
        return (float(close) / float(pre_close) - 1) * 100
    return None


def _exchange_of(code: str) -> str:
    """交易所前缀。快照接口有时只给 6 位数字代码，这里补上判断。"""
    text = (code or "").strip().upper()
    if text[:2] in ("SH", "SZ", "BJ"):
        return text[:2]
    if text.startswith(("6", "9", "5", "7")):
        return "SH"
    if text.startswith(("4", "8")):
        return "BJ"
    return "SZ"


def summarize(rows, trade_date: str, source: str, precise_limits: bool = False) -> dict | None:
    """把一批当日 K 线汇总成一行市场广度。没有涨跌幅数据就返回 None。

    口径：只有"有当日涨跌幅的票"进样本——停牌股当天没有 K 线，自动不计入，
    这和交易所公布的涨跌家数口径一致。
    """
    values = [(row, _pct(row)) for row in rows]
    sample = [(row, value) for row, value in values if value is not None]
    if not sample:
        return None
    pcts = [value for _, value in sample]
    up = sum(1 for value in pcts if value > 0)
    down = sum(1 for value in pcts if value < 0)
    flat = len(pcts) - up - down

    if precise_limits:
        limit_up = sum(1 for row, _ in sample if limits.at_limit_up(row, str(row.get("code") or "")))
        limit_down = sum(1 for row, _ in sample if limits.at_limit_down(row, str(row.get("code") or "")))
        # 炸板：盘中摸到涨停价，收盘却没封住（有 high 才判得了，快照那路没有）
        broken = sum(
            1 for row, _ in sample
            if row.get("high") is not None
            and not limits.at_limit_up(row, str(row.get("code") or ""))
            and limits.at_limit_price_hit(row, str(row.get("code") or ""))
        )
    else:
        limit_up = sum(1 for value in pcts if value >= 9.8)
        limit_down = sum(1 for value in pcts if value <= -9.8)
        broken = None

    amounts = [(_exchange_of(str(row.get("code") or "")), float(row.get("amount") or 0)) for row, _ in sample]
    return {
        "trade_date": trade_date,
        "coverage": len(pcts),
        "up_count": up,
        "down_count": down,
        "flat_count": flat,
        "limit_up_count": limit_up,
        "limit_down_count": limit_down,
        "broken_limit_count": broken,
        "max_boards": None,           # 由 board_streaks() 单独补：要跨日才知道连了几板
        "up_ratio": round(up / max(1, up + down), 4),
        "median_pct_chg": round(statistics.median(pcts), 4),
        "total_amount": sum(value for _, value in amounts),
        "sh_amount": sum(value for market, value in amounts if market == "SH"),
        "sz_amount": sum(value for market, value in amounts if market == "SZ"),
        "source": source,
        "updated_at": db.now_iso(),
    }


def from_snapshot(snapshot, trade_date: str, source_name: str) -> dict | None:
    """数据源快照给出的那一路（只有涨跌幅，没有前收盘价，所以用粗略的涨跌停口径）。"""
    return summarize([dict(row) for row in snapshot], trade_date, source_name, precise_limits=False)


def local_rows(conn, trade_date: str) -> list[dict]:
    """当天本地入库的**个股** K 线。

    只要个股：指数不是"家"，ETF 也不是——官方涨跌家数从来只数股票。
    代码表里没有类型的（历史遗留）按个股处理，免得把它们漏掉。
    """
    return [
        dict(row)
        for row in db.query(
            conn,
            """SELECT b.code, b.pct_chg, b.close, b.pre_close, b.high, b.low, b.amount
               FROM bars_daily b
               LEFT JOIN instruments i ON i.code = b.code
               WHERE b.trade_date=?
                 AND COALESCE(b.quality_flag,'ok')!='blocked'
                 AND (i.type IS NULL OR i.type = 'stock')""",
            (trade_date,),
        )
    ]


def from_local(conn, trade_date: str) -> dict | None:
    """用本地 K 线算广度。本地有几只票，就覆盖几只——数量会写进 source 备注里。"""
    rows = local_rows(conn, trade_date)
    return summarize(rows, trade_date, "local", precise_limits=True)


def board_streaks(conn, dates: list[str] | None = None, lookback: int = 30,
                  verbose: bool = False) -> dict[str, dict]:
    """连板高度与炸板家数：**纯本地算，不联网**。

    连板要跨日才知道（"今天几连板"= 往前数连续几个交易日收盘封在涨停），
    所以这里按 code 排序走一遍日线，维护每只票的"当前连板数"，
    到关心的日期就把当天最大值记下来。停牌（当天没 K 线）会让连板断掉——这是对的。

    炸板 = 盘中摸到涨停价、收盘没封住（见 limits.at_limit_price_hit）。
    """
    wanted = set(dates or [])
    sql = """SELECT code, trade_date, close, pre_close, high, low, pct_chg
             FROM bars_daily WHERE COALESCE(quality_flag,'ok')!='blocked'"""
    params: tuple = ()
    if wanted:
        start = db.query_one(
            conn,
            """SELECT trade_date FROM bars_daily WHERE trade_date <= ?
               GROUP BY trade_date ORDER BY trade_date DESC LIMIT 1 OFFSET ?""",
            (min(wanted), max(0, lookback)),
        )
        if start and start["trade_date"]:
            sql += " AND trade_date >= ?"
            params = (start["trade_date"],)
    sql += " ORDER BY code, trade_date"

    out: dict[str, dict] = {}
    # 连板要按**交易日**连续：中间停牌一天也算断（看这只票自己的上一根是不够的）。
    # 所以日历要从 trade_calendar 取——停牌那天它的 K 线压根不存在，拿行情里的日期当日历看不出断档。
    calendar = [
        r["trade_date"]
        for r in db.query(
            conn, "SELECT trade_date FROM trade_calendar WHERE is_trading_day=1 ORDER BY trade_date")
    ]
    if not calendar:
        calendar = [r["trade_date"] for r in db.query(
            conn, "SELECT DISTINCT trade_date FROM bars_daily ORDER BY trade_date")]
    order = {day: i for i, day in enumerate(calendar)}
    current_code = None
    streak = 0
    prev_date = None
    for row in db.query(conn, sql, params):
        bar = dict(row)
        code = bar["code"]
        if code != current_code:
            current_code, streak, prev_date = code, 0, None
        day = bar["trade_date"]
        if prev_date is not None and order.get(day, -9) != order.get(prev_date, -9) + 1:
            streak = 0                      # 中间隔了交易日 → 断了
        prev_date = day
        if bar.get("pre_close") is None and bar.get("pct_chg") is None:
            # 前收和涨跌幅都没有就判不了涨跌停（at_limit_* 两者都要一个）
            streak = 0
            continue
        if limits.at_limit_up(bar, code):
            streak += 1
        else:
            streak = 0
        key = day
        if wanted and key not in wanted:
            continue
        slot = out.setdefault(key, {"max_boards": 0, "broken_limit_count": 0, "streak_up_count": 0})
        if streak:
            slot["streak_up_count"] += 1
            slot["max_boards"] = max(slot["max_boards"], streak)
        if limits.at_limit_price_hit(bar, code) and streak == 0:
            slot["broken_limit_count"] += 1
    if verbose and out:
        print(f"  连板/炸板：{len(out)} 个交易日，最高 {max(v['max_boards'] for v in out.values())} 连板")
    return out


def backfill(conn, days: int | None = None, verbose: bool = False,
             min_coverage: int = DEFAULT_MIN_COVERAGE) -> dict:
    """把本地能算的交易日全部补进 market_breadth。

    已经有真实快照的日期不动（快照覆盖更全）；只有本地算的会被重算一遍，
    因为仓库每天在长大，同一历史日期后来能覆盖到更多票。
    """
    sql = "SELECT DISTINCT trade_date FROM bars_daily ORDER BY trade_date DESC"
    params: tuple = ()
    if days:
        sql += " LIMIT ?"
        params = (int(days),)
    dates = [row["trade_date"] for row in db.query(conn, sql, params)]
    if not dates:
        return {"ok": False, "message": "本地还没有日线，先跑一次 3-每日任务", "written": 0}

    have = {row["trade_date"]: (row["source"] or "") for row in db.query(
        conn, "SELECT trade_date, source FROM market_breadth")}
    streaks = board_streaks(conn, dates, verbose=verbose)
    written = skipped = thin = 0
    for trade_date in dates:
        existing = have.get(trade_date)
        if existing and not existing.startswith("local"):
            # 已有真实快照（覆盖更全）就保留广度本身，只把连板/炸板补进去
            slot = streaks.get(trade_date)
            if slot:
                conn.execute(
                    "UPDATE market_breadth SET broken_limit_count=?, max_boards=? WHERE trade_date=?",
                    (slot["broken_limit_count"], slot["max_boards"], trade_date),
                )
                conn.commit()
            skipped += 1
            continue
        record = from_local(conn, trade_date)
        if record is None:
            continue
        if record["coverage"] < min_coverage:
            thin += 1
            continue
        record.update(streaks.get(trade_date, {}))
        db.upsert_rows(conn, "market_breadth", [record], ["trade_date"])
        written += 1
    if verbose:
        print(f"  市场广度：写入 {written} 个交易日，跳过已有快照的 {skipped} 个")
        if thin:
            print(f"            另有 {thin} 个交易日样本不足 {min_coverage} 只，没写"
                  "（当天还没补全市场数据）")
    return {"ok": True, "written": written, "skipped": skipped, "thin": thin, "dates": len(dates)}
