"""全市场本地化：有空时把全市场的日线抓下来，之后每天只补一根。

三条腿，各管一段：
1. sync_universe  拉全市场代码表 → instruments（5000+ 行）
2. sync_history   逐只补历史。可中断、可续跑、可分批：已经跟到最新交易日的标的自动跳过，
                  所以反复双击就能慢慢填满。首次全量大概几十分钟（5000 多次请求）。
3. snapshot_bars  一次请求拿全市场当天快照，直接写成当日 K 线。
                  这是"每天只拉最新数据"的主力：1~2 个请求覆盖 5000+ 只。

一个必须知道的口径：快照价是**不复权**的实时价，baostock 回补的是前复权价。
所以快照只写给观察池以外的股票（本地筛选、翻看用）；进了观察池的票由 3-每日任务
用 baostock 覆盖成前复权，系统的信号一定建立在复权数据上。
"""

from __future__ import annotations

import shutil
import sqlite3
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from . import db, validate
from .sources import build_source

SNAPSHOT_SOURCE = "snapshot"
DEFAULT_HISTORY_DAYS = 750
DEFAULT_BATCH = 800
# 历史少于这个根数就认为"没补全"，下次整段重抓
# （北交所这类腾讯只给当日数据的，就靠这条兜住）
THIN_HISTORY = 20


def _source_for(cfg: dict, capability: str):
    """按能力挑源（和 tasks 里逻辑一致，这里独立实现以免循环导入）。"""
    seen: set[str] = set()
    for name in (cfg["sources"].get("primary"), cfg["sources"].get("backup"), cfg["sources"].get("fallback")):
        if not name or name in seen:
            continue
        seen.add(name)
        try:
            source = build_source(name, cfg)
        except Exception:
            continue
        if capability in getattr(source, "capabilities", set()):
            return source
    return None


def daily_sources(cfg: dict) -> list:
    """所有支持日线的源，按 主源 → 备份源 → 兜底源 → 补充源 排序。

    补充源（sources.extra）只在这里用，不参与日终任务的主备交叉校验——
    它们可能是不复权的（比如新浪），混进交叉校验会天天误报冲突。
    """
    out: list = []
    seen: set[str] = set()
    names = [
        cfg["sources"].get("primary"),
        cfg["sources"].get("backup"),
        cfg["sources"].get("fallback"),
        *(cfg["sources"].get("extra") or []),
    ]
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        try:
            source = build_source(name, cfg)
        except Exception:
            continue
        if "daily_bars" in getattr(source, "capabilities", set()):
            out.append(source)
    return out


def latest_trade_date(conn, cfg, fallback: str | None = None) -> str:
    """本地认得的最新交易日：优先交易日历，其次库里已有的日线。"""
    today = datetime.now().strftime("%Y-%m-%d")
    row = db.query_one(
        conn,
        "SELECT MAX(trade_date) AS d FROM trade_calendar WHERE is_trading_day=1 AND trade_date<=?",
        (today,),
    )
    if row and row["d"]:
        return row["d"]
    row = db.query_one(conn, "SELECT MAX(trade_date) AS d FROM bars_daily")
    if row and row["d"]:
        return row["d"]
    return fallback or today


def universe_codes(conn, include_watchlist: bool = True) -> list[str]:
    """本地代码表。默认把观察池也算进来。"""
    sql = "SELECT code FROM instruments"
    if not include_watchlist:
        sql += " WHERE COALESCE(in_watchlist,0)=0"
    return [row["code"] for row in db.query(conn, sql + " ORDER BY code")]


def sync_universe(conn, cfg, verbose: bool = True) -> dict:
    """拉全市场代码与名称，写进 instruments（in_watchlist=0 表示只是目录）。"""
    source = _source_for(cfg, "market_snapshot")
    directory = getattr(source, "symbol_directory", None) if source else None
    if directory is None:
        return {"ok": False, "message": "当前数据源不支持拉全市场代码表（需要 akshare）"}
    try:
        rows = source.symbol_directory()
    except Exception as exc:
        return {"ok": False, "message": f"拉代码表失败：{exc}"}
    if not rows:
        return {"ok": False, "message": "代码表为空"}

    payload = [
        {
            "code": item["code"],
            "name": item["name"],
            "type": "stock",
            "exchange": item["code"][:2],
            "in_watchlist": 0,
            "updated_at": db.now_iso(),
        }
        for item in rows
    ]
    db.upsert_rows(conn, "instruments", payload, ["code"])
    if verbose:
        print(f"  代码表：{len(payload)} 只")
    return {"ok": True, "codes": len(payload)}


def sync_history(conn, cfg, codes: list[str] | None = None, limit: int | None = None,
                 days: int | None = None, verbose: bool = True) -> dict:
    """逐只补日线。已经最新的自动跳过，所以可以反复跑、随时中断。"""
    sources = daily_sources(cfg)
    if not sources:
        return {"ok": False, "message": "没有支持日线的数据源"}
    config = cfg.get("warehouse") or {}
    target = latest_trade_date(conn, cfg)
    days = days or int(config.get("history_days", DEFAULT_HISTORY_DAYS))
    limit = limit if limit is not None else int(config.get("batch_size", DEFAULT_BATCH))

    all_codes = codes or universe_codes(conn)
    if not all_codes:
        return {"ok": False, "message": "本地还没有代码表，先跑一次全市场同步的代码表步骤"}

    last_by_code = {
        row["code"]: row["d"]
        for row in db.query(conn, "SELECT code, MAX(trade_date) AS d FROM bars_daily GROUP BY code")
    }
    history_rows = {
        row["code"]: row["n"]
        for row in db.query(conn, "SELECT code, COUNT(*) AS n FROM bars_daily GROUP BY code")
    }
    # 把标的类型也带上：指数不能按"成交量 × 点位"估算成交额，适配器需要知道类型
    kind_by_code = {
        row["code"]: (row["type"] or "stock")
        for row in db.query(conn, "SELECT code, type FROM instruments")
    }
    pending = [
        code
        for code in all_codes
        if (last_by_code.get(code) or "") < target or history_rows.get(code, 0) < THIN_HISTORY
    ]
    batch = pending[:limit] if limit else pending

    if verbose:
        print(f"  目标交易日 {target}；本地 {len(all_codes)} 只，待补 {len(pending)} 只，"
              f"本次处理 {len(batch)} 只")

    started = time.time()
    written = failed = 0
    dropped: set[str] = set()          # 连续失败太多次的源，本轮不再尝试
    strikes: dict[str, int] = {source.name: 0 for source in sources}
    first_error: str | None = None
    for index, code in enumerate(batch, 1):
        last = last_by_code.get(code)
        # 只有一根（或很薄）的标的要整段重补：光靠增量永远攒不出历史
        thin = history_rows.get(code, 0) < THIN_HISTORY
        if last and not thin:
            start = (datetime.strptime(last, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            start = (datetime.strptime(target, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
        if start > target:
            continue
        rows: list[dict] = []
        # 首次回补希望拿到足够长的历史；增量只要补上缺的那几天。
        # 达不到期望就继续问下一个源，最后取拿得最多的那个。
        want = 60 if (last is None or thin) else 1
        best: list[dict] = []
        for source in sources:
            if source.name in dropped:
                continue
            try:
                rows = source.daily_bars(code, start, target, kind_by_code.get(code, "stock"))
            except Exception as exc:
                rows = []
                strikes[source.name] += 1
                if first_error is None:
                    first_error = f"{source.name}：{type(exc).__name__} {exc}"
                if strikes[source.name] >= 3:
                    dropped.add(source.name)
                    if verbose:
                        print(f"    ! {source.name} 连续失败 3 次，本轮不再尝试它"
                              f"（原因见下面第一条失败信息）")
            if len(rows) > len(best):
                best = rows
            if rows:
                strikes[source.name] = 0
                if len(rows) >= want:
                    break
        rows = best
        if not rows:
            failed += 1
        else:
            checked = validate.validate_bars(rows, cfg)
            for row in checked.rows:
                row["source"] = row.get("source") or source.name
                row["updated_at"] = db.now_iso()
            db.upsert_rows(conn, "bars_daily", checked.rows, ["code", "trade_date"])
            written += 1
        if verbose and index % 50 == 0:
            speed = index / max(0.001, time.time() - started)
            left = (len(batch) - index) / max(0.001, speed)
            print(f"    {index}/{len(batch)} 已补 {written} 只，失败 {failed} 只，"
                  f"本批预计还要 {left / 60:.1f} 分钟")
        if len(dropped) == len(sources):
            # 所有源都不可用，再跑下去只是浪费你的时间
            if verbose:
                print(f"    ! 所有数据源都不可用，本批提前结束（已尝试 {index} 只）")
            break

    remaining = len(pending) - len(batch)
    result = {"ok": True, "target": target, "total": len(all_codes), "pending": len(pending),
              "done": len(batch), "written": written, "failed": failed, "remaining": remaining,
              "seconds": round(time.time() - started, 1), "first_error": first_error}
    if verbose:
        print(f"  本批完成：{written} 只写入、{failed} 只没数据，用时 {result['seconds']} 秒")
        if failed and first_error:
            print(f"  第一个失败原因：{first_error[:200]}")
            print("  （如果所有源都失败，先跑 5-安装真实数据源 做一次连通性自检）")
        if remaining:
            print(f"  还剩 {remaining} 只 —— 再跑一次接着补（补过的会自动跳过）")
        else:
            print("  全部标的都跟到最新交易日了")
    return result


def snapshot_bars(conn, cfg, trade_date: str | None = None, verbose: bool = True) -> dict:
    """用一次全市场快照，给观察池以外的股票补上当日 K 线。"""
    source = _source_for(cfg, "market_snapshot")
    if source is None:
        return {"ok": False, "message": "没有支持全市场快照的数据源"}
    trade_date = trade_date or latest_trade_date(conn, cfg)
    try:
        snapshot = source.market_snapshot(trade_date)
    except Exception as exc:
        return {"ok": False, "message": f"取全市场快照失败：{exc}"}
    if not snapshot:
        return {"ok": False, "message": "快照为空"}

    protected = {
        row["code"]
        for row in db.query(
            conn,
            "SELECT code FROM bars_daily WHERE trade_date=? AND COALESCE(source,'')!=?",
            (trade_date, SNAPSHOT_SOURCE),
        )
    }
    rows: list[dict] = []
    incomplete = 0
    for item in snapshot:
        code = item.get("code")
        close = item.get("close")
        if not code or not close or code in protected:
            continue
        # 开高低缺一个都不写：半根 K 线进了库，后面算均线/突破会直接炸
        if item.get("open") is None or item.get("high") is None or item.get("low") is None:
            incomplete += 1
            continue
        rows.append(
            {
                "code": code,
                "trade_date": trade_date,
                "open": item.get("open"),
                "high": item.get("high"),
                "low": item.get("low"),
                "close": close,
                "pre_close": item.get("pre_close"),
                "volume": item.get("volume"),
                "amount": item.get("amount"),
                "pct_chg": item.get("pct_chg"),
                "adj_factor": 1.0,
                "close_adj": close,
                "source": SNAPSHOT_SOURCE,
                "quality_flag": "ok",
                "updated_at": db.now_iso(),
            }
        )
    if not rows:
        return {"ok": False, "message": "快照里没有可写入的标的"}
    db.upsert_rows(conn, "bars_daily", rows, ["code", "trade_date"])
    db.upsert_rows(
        conn,
        "instruments",
        [
            {"code": row["code"], "name": row["code"], "type": "stock",
             "exchange": row["code"][:2], "in_watchlist": 0, "updated_at": db.now_iso()}
            for row in rows
        ],
        ["code"],
    )
    if verbose:
        print(f"  快照写库：{trade_date} 写入 {len(rows)} 只，"
              f"跳过已有正式数据的 {len(protected)} 只、字段不全的 {incomplete} 只")
    return {"ok": True, "trade_date": trade_date, "written": len(rows),
            "skipped": len(protected), "incomplete": incomplete}


def status(conn) -> dict:
    """本地仓库覆盖情况：同步前先看这个，心里有数。"""
    total = db.query_one(conn, "SELECT COUNT(*) AS n FROM instruments")["n"]
    with_data = db.query_one(conn, "SELECT COUNT(DISTINCT code) AS n FROM bars_daily")["n"]
    bars = db.query_one(conn, "SELECT COUNT(*) AS n FROM bars_daily")["n"]
    last = db.query_one(conn, "SELECT MAX(trade_date) AS d FROM bars_daily")["d"]
    snapshot_only = db.query_one(
        conn,
        """SELECT COUNT(*) AS n FROM (
               SELECT code FROM bars_daily GROUP BY code
                HAVING SUM(CASE WHEN COALESCE(source,'')!='snapshot' THEN 1 ELSE 0 END)=0
           )""",
    )["n"]
    return {"instruments": total, "with_data": with_data, "bars": bars,
            "latest": last, "snapshot_only": snapshot_only}


# ---------- 打包带走 ----------

def pack_database(cfg: dict, out_dir: str | Path | None = None, compress: bool = True,
                  verbose: bool = True) -> dict:
    """把数据库打包成一个可以拷到另一台电脑的文件。

    为什么可以整包搬：SQLite 是单文件、内部不存绝对路径，Windows 和 Mac 通用；
    而且这个库用的是普通日志模式（不是 WAL），没有 -wal / -shm 旁挂文件，
    只要没有程序正在写，直接复制出来的就是一致的快照。

    打包前会先抢一次写锁：抢不到说明 DB Browser / 看盘页面 / 某个任务正在用，
    这时候复制可能拿到写了一半的状态，所以直接让你先关掉。
    """
    source = Path(cfg["_db_path"])
    if not source.exists():
        return {"ok": False, "message": f"数据库不存在：{source}"}

    conn = db.connect(source)
    try:
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            return {"ok": False,
                    "message": "数据库正被其他程序占用（DB Browser / 看盘页面 / 正在跑的任务），"
                               "先关掉它们再打包——不然拿到的可能是写了一半的状态。"}
        check = conn.execute("PRAGMA quick_check").fetchone()[0]
        stats = {
            "instruments": db.query_one(conn, "SELECT COUNT(*) AS n FROM instruments")["n"],
            "with_data": db.query_one(conn, "SELECT COUNT(DISTINCT code) AS n FROM bars_daily")["n"],
            "bars": db.query_one(conn, "SELECT COUNT(*) AS n FROM bars_daily")["n"],
            "latest": (db.query_one(conn, "SELECT MAX(trade_date) AS d FROM bars_daily") or {"d": None})["d"],
        }
        version_row = db.query_one(conn, "SELECT feature_version FROM rule_version LIMIT 1")
        version = version_row["feature_version"] if version_row else None
    finally:
        conn.close()

    target_dir = Path(out_dir) if out_dir else Path(cfg["_project_root"]) / "备份"
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    note = (
        "A 股交易提醒系统 · 数据库快照\n"
        f"打包时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"标的：代码表 {stats['instruments']} 只，其中有日线 {stats['with_data']} 只\n"
        f"日线：{stats['bars']:,} 根，最新 {stats['latest'] or '—'}\n"
        f"规则版本：{version or '—'}\n"
        f"完整性检查：{check}\n\n"
        "怎么用：把 market.db 放到项目的 data/ 目录下（覆盖同名文件），\n"
        "        然后双击 14-看盘页面 就能看；想继续抓数据就双击 3-每日任务。\n"
        "注意：换到另一台电脑后，先双击 5-安装真实数据源 确认那边的网络能连上数据源。\n"
    )

    if compress:
        target = target_dir / f"market-{stamp}.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(source, "data/market.db")
            # 压缩包里的文件名用 ASCII：中文名在部分解压工具（尤其 Windows 自带）会变乱码
            archive.writestr("README.txt", note)
    else:
        target = target_dir / f"market-{stamp}.db"
        shutil.copy2(source, target)
        (target_dir / f"market-{stamp}-说明.txt").write_text(note, encoding="utf-8")

    if verbose:
        print(f"  已打包：{target}")
        print(f"  原始 {source.stat().st_size / 1048576:.0f} MB → "
              f"{target.stat().st_size / 1048576:.0f} MB")
    return {"ok": check == "ok", "path": target, "source_size": source.stat().st_size,
            "target_size": target.stat().st_size, "stats": stats, "check": check}
