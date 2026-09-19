"""新股与次新：单独一摊，因为它们跟其它票不是一回事。

两件事促成了这个模块：

  1. **它们进不了现有的筛选池**。筛选要求至少 80 根日线（回测要 120 根），刚上市的票
     天然不满足——不是被筛掉，是根本没法参与。可这恰恰是关注度最高、最容易
     被"涨停板故事"带着走的一批。
  2. **推送里该有它们**。每天扫一遍"谁上市了、次新里谁有动静"，比事后翻代码表有用。

判定依据是**本地日线的第一根**：同步一次要 750 天历史，某个代码只拿到 N 根，
就说明它上市大约 N 个交易日。这个推断有个前提——同步是完整的：半途同步的票
日线还没补上，这里会把它当新股。所以 sync 没跑完之前，这张表只能当参考。

这一层同样只描述事实：上市多久、几个板、上市以来涨了多少、现在能不能建仓。
不给"要不要打新""能不能追"这类判断。
"""

from __future__ import annotations

from datetime import datetime

from . import db, limits

DEFAULTS = {
    "new_days": 30,      # 上市 ≤ 30 个交易日算新股
    "recent_days": 250,  # ≤ 250 个交易日（约一年）算次新
    "limit": 80,         # 页面一次列多少只
    "push_top": 6,       # 推送里每类最多列几只
    "types": ["stock"],  # 只看个股：新 ETF 天天有，混进来会把新股淹没
    "ipo_per_run": 200,  # 日终每轮补多少个真实上市日（baostock 一只 0.2~0.5 秒）
}


def settings(cfg: dict | None) -> dict:
    merged = dict(DEFAULTS)
    merged.update((cfg or {}).get("newstock") or {})
    return merged


def board_of(code: str) -> str:
    """板块：决定涨跌停幅度，也是新股最该看的一张标签。"""
    text = (code or "").strip().upper()
    symbol = text[2:] if text[:2] in ("SH", "SZ", "BJ") else text
    if symbol.startswith(("688", "689")):
        return "科创板"
    if symbol.startswith(("300", "301", "302")):
        return "创业板"
    if text.startswith("BJ") or symbol.startswith(("43", "83", "87", "88", "92")):
        return "北交所"
    return "主板"


def ipo_dates(conn) -> dict[str, str]:
    """已经拿到的权威上市日（instruments.listed_date）。"""
    return {
        row["code"]: row["listed_date"]
        for row in db.query(conn, "SELECT code, listed_date FROM instruments WHERE listed_date IS NOT NULL")
    }


def sync_ipo_dates(conn, cfg: dict | None = None, limit: int = 300, verbose: bool = False) -> dict:
    """从 baostock 补权威上市日，写进 instruments.listed_date。

    为什么值得单独做这一步：新股原本靠"本地日线只有几根"来推，可**同步还没轮到的票
    一根日线都没有**——一只上市三天的新股，在全市场同步跑到它之前是看不见的。
    拿到上市日之后，"是不是新股"就跟同步进度无关了。

    分批、可续跑：每次只补 limit 个还没日期的沪深个股。baostock 不覆盖北交所，
    那部分继续靠日线推算（查询里直接排除，免得永远补不完）。
    """
    from .sources import split_code
    from .sources.baostock_source import BaostockSource

    rows = db.query(
        conn,
        """SELECT code FROM instruments
           WHERE type='stock' AND listed_date IS NULL AND code NOT LIKE 'BJ%'
           ORDER BY code LIMIT ?""",
        (int(limit),),
    )
    codes = [row["code"] for row in rows]
    if not codes:
        return {"ok": True, "updated": 0, "remaining": 0}

    source = BaostockSource(cfg or {})
    updated = 0
    for code in codes:
        try:
            info = source.stock_basic(code)
        except Exception as exc:
            if verbose:
                print(f"    ! 上市日补到 {code} 就断了：{type(exc).__name__} {exc}")
            break                       # 断网或限流：本轮到此为止，下次接着补
        listed = (info or {}).get("ipoDate") or ""
        if not listed:
            continue
        db.upsert_rows(
            conn,
            "instruments",
            [{"code": code, "listed_date": listed, "updated_at": db.now_iso()}],
            ["code"],
        )
        updated += 1

    remaining = db.query_one(
        conn,
        """SELECT COUNT(*) AS n FROM instruments
           WHERE type='stock' AND listed_date IS NULL AND code NOT LIKE 'BJ%'""",
    )["n"]
    if verbose:
        print(f"  上市日：本轮补 {updated} 只，还剩 {remaining} 只待补（分批跑，反复运行即可补完）")
    return {"ok": True, "updated": updated, "remaining": int(remaining)}


def _trading_days_since(conn, listed: str, latest: str | None, bars_loaded: int) -> int:
    """上市以来经过了多少个交易日：有日线就直接数日线，没有就用交易日历，再没有按日历折算。"""
    if bars_loaded:
        return bars_loaded
    if not latest:
        return 0
    row = db.query_one(
        conn,
        """SELECT COUNT(*) AS n FROM trade_calendar
           WHERE is_trading_day=1 AND trade_date>=? AND trade_date<=?""",
        (listed, latest),
    )
    if row and row["n"]:
        return int(row["n"])
    try:
        span = (datetime.strptime(latest, "%Y-%m-%d") - datetime.strptime(listed, "%Y-%m-%d")).days
    except ValueError:
        return 0
    return max(0, int(span * 0.69))     # 一周五个交易日 ≈ 0.69


def refresh(conn, cfg: dict | None = None, verbose: bool = False) -> dict:
    """按"权威上市日 + 日线兜底"重算新股/次新，写进 new_listings。

    两条路并起来用，缺一不可：
      · **权威上市日**（instruments.listed_date，来自 baostock）：只要上市日在窗口内就算，
        哪怕本地还没有它的日线——同步没跑到的票也能出现在名单里；
      · **日线兜底**：没有上市日的（北交所、还没补到的），用"本地日线只有 N 根"来推。
    """
    conf = settings(cfg)
    new_days, recent_days = int(conf["new_days"]), int(conf["recent_days"])
    wanted = {str(kind) for kind in (conf["types"] or ["stock"])}

    latest_row = db.query_one(conn, "SELECT MAX(trade_date) AS d FROM bars_daily")
    latest = latest_row["d"] if latest_row else None

    catalog = {
        row["code"]: dict(row)
        for row in db.query(conn, "SELECT code, name, type, listed_date FROM instruments")
    }

    # 候选一：权威上市日在窗口内的个股（日历日放宽 1.6 倍，覆盖周末与节假日）
    candidates: dict[str, dict] = {}
    for code, meta in catalog.items():
        if (meta.get("type") or "stock").lower() not in wanted:
            continue
        listed = meta.get("listed_date")
        if not listed or not latest:
            continue
        try:
            age = (datetime.strptime(latest, "%Y-%m-%d") - datetime.strptime(listed, "%Y-%m-%d")).days
        except ValueError:
            continue
        if age <= recent_days * 1.6:
            candidates[code] = {"listed_date": listed, "estimated": 0}

    # 候选二：本地日线根数 ≤ recent_days 的个股（还没拿到权威上市日的那些）。
    # **不过滤 blocked**：新股上市首日的真实大涨会触发日终的跳变保护、被标成 blocked，
    # 那是"这根值不值得信"的标记，不是"这天没交易"——按它过滤，新股会整批消失。
    # **要过滤 snapshot**：日终的全市场快照会给几千只还没补历史的票各写一根当日 K 线，
    # 那些票只有一根 bar，按根数算会全部变成"上市 1 天"。
    counts = {
        row["code"]: row
        for row in db.query(
            conn,
            """SELECT b.code, COUNT(*) AS bars, MIN(b.trade_date) AS first_date,
                      MAX(b.trade_date) AS last_date
               FROM bars_daily b
               WHERE COALESCE(b.source, '') != 'snapshot'
               GROUP BY b.code HAVING COUNT(*) <= ?""",
            (recent_days,),
        )
    }
    for code, info in counts.items():
        meta = catalog.get(code)
        if meta is None or (meta.get("type") or "stock").lower() not in wanted:
            continue
        candidates.setdefault(code, {"listed_date": info["first_date"], "estimated": 1})

    payload: list[dict] = []
    latest_seen = latest
    for code, meta in candidates.items():
        row = counts.get(code)
        info = dict(row) if row else {}
        bars_loaded = int(info.get("bars") or 0)
        bars = [
            dict(row)
            for row in db.query(
                conn,
                """SELECT trade_date, open, high, low, close, pre_close, pct_chg, quality_flag
                   FROM bars_daily WHERE code=? AND COALESCE(source,'') != 'snapshot'
                   ORDER BY trade_date""",
                (code,),
            )
        ]
        name = (catalog.get(code) or {}).get("name") or code
        listed = meta["listed_date"]
        trading_days = _trading_days_since(conn, listed, latest, bars_loaded)
        stage = "new" if trading_days <= new_days else "recent"

        blocked = sum(1 for bar in bars if (bar.get("quality_flag") or "ok") == "blocked")
        # "上市以来涨跌"只从**没被标记**的那一段算：新股首日常常被跳变保护标成 blocked
        # （来源给的 pre_close 不可信），拿它当基准会算出 +177% 这种数字。
        clean = [bar for bar in bars if (bar.get("quality_flag") or "ok") != "blocked"]
        first_close = clean[0].get("close") if clean else None
        last_close = clean[-1].get("close") if clean else None
        since = None
        if first_close and last_close:
            # 有日线、且上市日就是第一根：直接算；否则用权威上市日之后的完整序列
            since = round((last_close / first_close - 1) * 100, 2)
        # 上市阶段要带上：新股头几天不设涨跌幅，那几天不该算"涨停"（连板也是）
        limit_ups = sum(
            1 for index, bar in enumerate(bars)
            if limits.at_limit_up(bar, code, name, trading_days=index + 1)
        )
        boards = 0
        for index, bar in enumerate(bars):
            if limits.at_limit_up(bar, code, name, trading_days=index + 1):
                boards += 1
            else:
                break

        payload.append({
            "code": code,
            "name": name,
            "board": board_of(code),
            "listed_date": listed,
            "trading_days": trading_days,
            "stage": stage,
            "first_close": first_close,
            "last_close": last_close,
            "since_list_pct": since,
            "limit_up_days": limit_ups,
            "boards_from_start": boards,
            "bars_loaded": bars_loaded,
            "blocked_bars": blocked,
            "estimated": meta["estimated"],
            "last_seen": info.get("last_date") or latest,
            "updated_at": db.now_iso(),
        })

    if payload:
        db.upsert_rows(conn, "new_listings", payload, ["code"])
        placeholders = ",".join("?" for _ in payload)
        conn.execute(
            f"UPDATE new_listings SET stage='old' WHERE code NOT IN ({placeholders})",
            [item["code"] for item in payload],
        )
        conn.commit()

    new_count = sum(1 for item in payload if item["stage"] == "new")
    waiting = sum(1 for item in payload if not item["bars_loaded"])
    if verbose:
        print(f"  新股/次新：新股 {new_count} 只、次新 {len(payload) - new_count} 只"
              f"（其中 {waiting} 只还没同步到日线，判定日期 {latest_seen or '—'}）")
    return {"ok": True, "total": len(payload), "new": new_count,
            "recent": len(payload) - new_count, "waiting": waiting, "as_of": latest_seen}


def scan(conn, cfg: dict | None = None, limit: int | None = None) -> dict:
    """读表 + 补上"现在什么样"：流动性、最近有没有提醒。"""
    from . import universe

    conf = settings(cfg)
    limit = int(limit or conf["limit"])
    rows = [
        dict(row)
        for row in db.query(
            conn,
            """SELECT * FROM new_listings WHERE stage IN ('new','recent')
               ORDER BY trading_days ASC, code""",
        )
    ]
    if not rows:
        return {"ok": False, "message": "还没有新股数据，先跑一次：python run.py listings"}

    threshold = universe.min_avg_amount(cfg)
    latest = db.query_one(conn, "SELECT MAX(trade_date) AS d FROM bars_daily")
    latest = latest["d"] if latest else None
    for item in rows:
        item["avg_amount_60d"] = universe.avg_amount(conn, item["code"])
        item["liquid"] = (threshold <= 0 or item["avg_amount_60d"] is None
                          or item["avg_amount_60d"] >= threshold)
        alert = db.query_one(
            conn,
            """SELECT level, message FROM alerts WHERE code=?
               ORDER BY trade_date DESC, level LIMIT 1""",
            (item["code"],),
        )
        item["alert"] = dict(alert) if alert else None

    new = [item for item in rows if item["stage"] == "new"][:limit]
    recent = [item for item in rows if item["stage"] == "recent"][:limit]
    return {
        "ok": True,
        "as_of": latest,
        "threshold": threshold,
        "counts": {"new": sum(1 for item in rows if item["stage"] == "new"),
                   "recent": sum(1 for item in rows if item["stage"] == "recent")},
        "new_days": conf["new_days"],
        "recent_days": conf["recent_days"],
        "new": new,
        "recent": recent,
    }


def today_listings(conn, trade_date: str) -> list[dict]:
    """在 trade_date 这一天**首次出现**的标的（推送里"今日新上市"用）。"""
    return [
        dict(row)
        for row in db.query(
            conn,
            """SELECT code, name, board, trading_days, since_list_pct
               FROM new_listings WHERE listed_date=? ORDER BY code""",
            (trade_date,),
        )
    ]


def highlights(conn, cfg: dict | None, trade_date: str, top: int | None = None) -> list[dict]:
    """次新里值得在推送里提一句的：先看有没有提醒，再看上市以来的强度，最后看成交额。"""
    conf = settings(cfg)
    top = int(top or conf["push_top"])
    rows = [
        dict(row)
        for row in db.query(
            conn,
            """SELECT code, name, board, trading_days, listed_date, since_list_pct,
                      limit_up_days, boards_from_start
               FROM new_listings WHERE stage='recent'""",
        )
    ]
    alert_codes = {
        row["code"]
        for row in db.query(conn, "SELECT DISTINCT code FROM alerts WHERE trade_date=?", (trade_date,))
    }
    for item in rows:
        item["alerted"] = item["code"] in alert_codes
    rows.sort(key=lambda item: (
        0 if item["alerted"] else 1,
        -(item["since_list_pct"] or -999),
        item["code"],
    ))
    return rows[:top]
