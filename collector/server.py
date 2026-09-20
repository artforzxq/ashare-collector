"""本地看盘页面：K 线 + 状态 + 关键带 + 因子 + 提醒，并且能自己加自选。

只用标准库起一个**本机**服务（127.0.0.1，不对外），页面和接口都由它提供：

    python run.py web          # 打开 http://127.0.0.1:8765

设计约束：页面不依赖任何外部库和 CDN——K 线是页面里手写的 canvas 绘制，
断网也能看历史；只有"添加新标的"这一件事需要联网取数。
"""

from __future__ import annotations

import json
import re
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import (candles as candles_mod, db, flow as flow_mod, market_time, notify, tasks,
               warehouse as warehouse_mod)
from . import jobs as jobs_mod
from .config import watchlist_codes
from .names import display_name
from .sources import build_source
from .sources.base import classify_symbol, exchange_of

WEB_DIR = Path(__file__).resolve().parent / "web"
# 筛选池要现算风险层（池外的票没有日终结果），一次接近 1 秒；切标签页时没必要重算。
# 20 秒的缓存 + 任务启动时作废，足够让手动刷新拿到新数据。
POOL_CACHE_SECONDS = 20
INDEX_FILE = WEB_DIR / "index.html"

# 进程启动时间：用来判断"页面比代码新"（改了后端没重启服务）
PROCESS_STARTED_TS = time.time()
PROCESS_STARTED_AT = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

WATCHLIST_KEYS = {"index": "indices", "etf": "etfs", "stock": "stocks"}
DEFAULT_KIND = {"indices": "index", "etfs": "etf", "stocks": "stock"}


# ---------- 代码规整 ----------

def normalize_code(raw: str) -> str | None:
    """把用户输入整成 SH600519 这种写法。认不出来返回 None。"""
    text = re.sub(r"\s+", "", str(raw or "")).upper()
    if not text:
        return None
    if "." in text:                                  # 600519.SH / SH.600519
        left, _, right = text.partition(".")
        if right in ("SH", "SZ", "BJ") and left.isdigit():
            text = right + left
        elif left in ("SH", "SZ", "BJ") and right.isdigit():
            text = left + right
    if len(text) > 2 and text[:2] in ("SH", "SZ", "BJ") and text[2:].isdigit():
        return text[:2] + text[2:].zfill(6)
    if text.isdigit():
        symbol = text.zfill(6)
        return exchange_of(symbol) + symbol
    return None


def guess_kind(code: str) -> str:
    """猜标的类型：指数 / ETF / 个股。"""
    text = code.upper()
    symbol = text[2:]
    if text.startswith("SH") and symbol.startswith(("000", "950")):
        return "index"
    if text.startswith("SZ") and symbol.startswith("399"):
        return "index"
    if symbol.startswith(("15", "16", "18", "50", "51", "52", "53", "56", "58")):
        return "etf"
    return "stock"


def _rewrite_watchlist(cfg_path: Path, cfg: dict) -> None:
    """只重写 watchlist 里的三行，配置文件里其它注释和排版原样保留。"""
    text = cfg_path.read_text(encoding="utf-8")
    for key in ("indices", "etfs", "stocks"):
        values = cfg["watchlist"].get(key) or []
        rendered = "[" + ", ".join(values) + "]"
        text, count = re.subn(
            rf"^([ \t]*{key}:[ \t]*).*$", lambda m, r=rendered: m.group(1) + r, text, count=1, flags=re.M
        )
        if not count:
            raise RuntimeError(f"配置文件里找不到 {key}: 这一行，请手工加上")
    cfg_path.write_text(text, encoding="utf-8")


# ---------- 应用层 ----------

def _latest_intraday_day(conn, code: str) -> str | None:
    row = db.query_one(
        conn,
        "SELECT MAX(substr(dt, 1, 10)) AS day FROM bars_intraday WHERE code=?",
        (code,),
    )
    return row["day"] if row and row["day"] else None


def _intraday_rows(conn, code: str, day: str) -> list[dict]:
    return [
        dict(row)
        for row in db.query(
            conn,
            """SELECT dt, close, volume, amount, source FROM bars_intraday
               WHERE code=? AND dt LIKE ? ORDER BY dt""",
            (code, f"{day}%"),
        )
    ]


def _prev_close(conn, code: str, day: str) -> float | None:
    row = db.query_one(
        conn,
        "SELECT close FROM bars_daily WHERE code=? AND trade_date < ? ORDER BY trade_date DESC LIMIT 1",
        (code, day),
    )
    return float(row["close"]) if row and row["close"] else None


def _freshness_payload(cfg: dict) -> dict:
    """数据有多新：日线到哪天、分时到几点、上次日终/筛选是什么时候。

    页面顶部的状态条读它。判断"这条数据能不能用"第一步就是看它有多新，
    所以这里把各档数据分别给出来，而不是揉成一句话。
    """
    conn = db.connect(cfg["_db_path"])
    try:
        def latest(table: str, column: str = "trade_date"):
            row = db.query_one(conn, f"SELECT MAX({column}) AS v FROM {table}")
            return row["v"] if row and row["v"] else None

        intraday = db.query_one(conn, "SELECT MAX(dt) AS v FROM bars_intraday")
        now = datetime.now()
        session = market_time.describe(conn, now)
        last_daily = db.query_one(
            conn,
            "SELECT MAX(created_at) AS v FROM data_health WHERE task LIKE 'daily%'",
        )
        last_screen = db.query_one(
            conn,
            "SELECT MAX(created_at) AS v FROM data_health WHERE task='screen'",
        )
        # 分时滞后多少分钟：交易时段里这个数字大就说明没跟上（比如上午的数据看到下午）
        intraday_age = None
        if intraday and intraday["v"]:
            try:
                stamp = datetime.strptime(str(intraday["v"])[:16], "%Y-%m-%d %H:%M")
                intraday_age = int((now - stamp).total_seconds() // 60)
            except ValueError:
                intraday_age = None
        daily_date = latest("bars_daily")
        return {
            "server_time": db.now_iso(),
            "session": session,
            "trading": market_time.is_trading_now(conn),
            "daily": daily_date,
            # 今天这根日线是不是"半根"：盘中就没收盘，别当成当日收盘价看
            "daily_partial": bool(daily_date == now.strftime("%Y-%m-%d")
                                  and session in ("交易中", "午休", "未开盘")),
            "features": latest("features_daily"),
            "breadth": latest("market_breadth"),
            "alerts": latest("alerts"),
            "screen": latest("screen_results"),
            "intraday": intraday["v"] if intraday else None,
            "intraday_age_min": intraday_age,
            "last_daily_run": last_daily["v"] if last_daily else None,
            "last_screen_run": last_screen["v"] if last_screen else None,
        }
    finally:
        conn.close()


class App:
    QUOTE_TTL = 20.0        # 报价缓存秒数：防止多个标签页把免费源刷爆
    POOL_TTL = 20.0         # 筛选池缓存秒数：它要现算风险层，切标签页不该重算

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.jobs = jobs_mod.JobManager()
        self._quote_cache: dict = {}
        self._pool_cache: tuple[float, dict] | None = None
        self._newstock_cache: tuple[float, dict] | None = None

    def quotes(self, codes: list | None = None) -> dict:
        """批量实时报价。只给盯盘用：不落库、不参与任何结论、失败也不影响别的。

        免费源有请求间隔的规矩，所以 20 秒内重复问同一批代码直接给缓存。
        """
        want = [str(code).strip().upper() for code in (codes or []) if str(code).strip()]
        if not want:
            want = [item["code"] for item in watchlist_codes(self.cfg)]

        cached = self._quote_cache
        now = time.time()
        if cached and cached.get("codes") == want and now - cached.get("at", 0) < self.QUOTE_TTL:
            payload = dict(cached["payload"])
            payload["cached"] = True
            return payload

        payload = {"items": [], "fetched_at": db.now_iso(), "cached": False, "error": None}
        conn = db.connect(self.cfg["_db_path"])
        try:
            payload["session"] = market_time.describe(conn)
            payload["trading"] = market_time.is_trading_now(conn)
        finally:
            conn.close()

        try:
            source = tasks.quote_source(self.cfg)
        except Exception as exc:            # 适配器导入失败之类，别把页面带崩
            payload["error"] = f"数据源不可用：{exc}"
            return payload
        if source is None:
            payload["error"] = "没有支持实时报价的数据源（检查 config.yaml 的 sources）"
            return payload
        try:
            payload["items"] = source.intraday_snapshot(want)
        except Exception as exc:
            payload["error"] = str(exc)
            return payload
        self._quote_cache = {"codes": want, "at": now, "payload": payload}
        return payload

    def freshness(self) -> dict:
        return _freshness_payload(self.cfg)

    # ---- 因子台账 / 参数回测 ----

    def factors(self) -> dict:
        """因子台账：谁在打分、权重多大、影子因子够不够格转正。

        数据全部来自本地库：factor_registry 是"在册名单"，factor_contributions 是
        "每个交易日实际记了什么"，相关性与 IC 由 promotion 现算。
        整段都是只读，所以样本大了最多是这个接口慢，不影响别的接口。
        """
        from . import promotion
        from .registry import FactorRegistry

        conn = db.connect(self.cfg["_db_path"])
        try:
            row = db.query_one(conn, "SELECT MAX(trade_date) AS d FROM factor_contributions")
            as_of = row["d"] if row else None
            items: list[dict] = []
            note = ""
            try:
                items = promotion.factor_stats(conn, self.cfg)
            except Exception as exc:      # 空库、样本太少都不该让页面白屏
                note = f"体检没算出来：{type(exc).__name__} {exc}"

            by_status: dict[str, int] = {}
            for item in items:
                by_status[item["status"]] = by_status.get(item["status"], 0) + 1

            violations: list[str] = []
            try:
                registry = FactorRegistry(
                    self.cfg.get("factors", []),
                    str(self.cfg["project"].get("feature_version", "v1")),
                )
                violations = registry.category_budget_violations()
            except Exception as exc:
                note = note or f"权重预算没检查成：{exc}"

            return {
                "ok": True,
                "as_of": as_of,
                "items": items,
                "by_status": by_status,
                "shadow_days": promotion.SHADOW_DAYS,
                "duplicate_corr": promotion.DUPLICATE_CORR,
                "max_days": max((item["days"] for item in items), default=0),
                "violations": violations,
                "note": note,
            }
        finally:
            conn.close()

    def backtest(self) -> dict:
        """最近一次参数回测的结果。没跑过就直说怎么跑，不假装有数据。"""
        from . import backtest as backtest_mod

        data = backtest_mod.latest_result(self.cfg["_project_root"])
        if not data:
            return {
                "ok": False,
                "message": "还没跑过参数回测",
                "hint": "它会用生产代码里同一套状态机，把参数网格放到本地历史上重跑一遍，"
                        "告诉你哪组参数真有超额、哪组只是拟合出来的尖峰。",
            }
        results = data.get("results") or []
        turnover = backtest_mod.latest_turnover(self.cfg["_project_root"])
        return {
            "ok": True,
            "saved_at": data.get("_saved_at"),
            "file": data.get("_file"),
            "current": data.get("current"),
            "sample": data.get("sample"),
            "years": data.get("years"),
            "bars": data.get("bars"),
            "code_count": data.get("code_count"),
            "cost": data.get("cost"),
            "limit_check": data.get("limit_check"),
            "plateau": data.get("plateau"),
            "items": results[:40],
            "total": len(results),
            "turnover": turnover,      # 换手率研究（同一次任务里一起跑的）
        }

    def analysis(self, code: str = "", scope: str = "") -> dict:
        """最近一次 AI 分析（百炼）。没有就直说，不假装有。

        注意定位：分析是**旁注**，读的是本地算好的数字，不回写任何结论。
        所以这里只负责把存过的那一条拿出来，外加"能不能再跑"的开关状态。

        scope：instrument 单只标的（要看 code）/ ledger 因子台账 / backtest 回测摘要 /
        market 资金去向（宽基与行业 ETF 份额、成交额、融资余额、市场层）。
        """
        from . import analysis as analysis_mod

        scope = scope or analysis_mod.SCOPE_INSTRUMENT
        if scope == analysis_mod.SCOPE_INSTRUMENT and not code:
            return {"ok": False, "message": "没指定标的"}
        conn = db.connect(self.cfg["_db_path"])
        try:
            conf = analysis_mod.settings(self.cfg)
            key, source = analysis_mod.resolve_key(self.cfg)
            row = analysis_mod.latest(conn, code, scope=scope)
            return {
                "ok": True,
                "scope": scope,
                "enabled": conf["enabled"],
                "configured": bool(key),
                "key_source": source,
                "model": conf["model"],
                "item": (
                    {
                        "code": row["code"] or {
                            analysis_mod.SCOPE_LEDGER: "因子台账",
                            analysis_mod.SCOPE_BACKTEST: "回测摘要",
                            analysis_mod.SCOPE_MARKET: "资金去向",
                        }.get(scope, "分析"),
                        "trade_date": row["trade_date"],
                        "model": row["model"],
                        "created_at": row["created_at"],
                        "answer": row["answer"],
                        "tokens": (row["prompt_tokens"] or 0) + (row["answer_tokens"] or 0),
                        "latency_ms": row["latency_ms"],
                    }
                    if row
                    else None
                ),
            }
        finally:
            conn.close()

    # ---- 页面上的任务按钮 ----

    def support(self) -> dict:
        """疑似托底：当天的判定（现算）+ 最近命中过的日子（库里留的）。

        只描述事实：份额净流入 + 异常放量同时成立才算命中。份额 T+1 披露，
        所以这是事后信号，页面别把它显示得像实时提示。
        """
        from . import support as support_mod

        conn = db.connect(self.cfg["_db_path"])
        try:
            today = support_mod.scan(conn, self.cfg)
            days = int(support_mod.settings(self.cfg)["history_days"])
            return {
                "ok": bool(today.get("ok")),
                "message": today.get("message", ""),
                "today": today if today.get("ok") else None,
                "history": support_mod.history(conn, days),
            }
        except Exception as exc:
            return {"ok": False, "message": f"读不到托底判定：{type(exc).__name__} {exc}"}
        finally:
            conn.close()

    def newstock(self) -> dict:
        """新股与次新：单独一摊，因为它们进不了筛选标的池（历史不够长）。

        只读表里的判定结果 + 补上流动性、最近提醒。判定口径在 collector/newstock.py。
        """
        from . import newstock as newstock_mod

        now = time.time()
        if self._newstock_cache and now - self._newstock_cache[0] < self.POOL_TTL:
            return self._newstock_cache[1]

        conn = db.connect(self.cfg["_db_path"])
        try:
            # 先重算一遍：这张表是快照，同步还在往里塞新票，算一次只要零点几秒。
            # 不这么做的话，一只刚上市的新股要等下一次日终才会出现在页面上。
            newstock_mod.refresh(conn, self.cfg)
            payload = newstock_mod.scan(conn, self.cfg)
        except Exception as exc:
            return {"ok": False, "message": f"读不到新股数据：{type(exc).__name__} {exc}"}
        finally:
            conn.close()
        if not payload.get("ok"):
            payload.setdefault("hint", "跑一次 3-每日任务 或 18-全市场同步 之后就有了")
        self._newstock_cache = (now, payload)
        return payload

    def pool(self, force: bool = False) -> dict:
        """筛选池：三段拼起来，回答"我现在该看什么"。

        ① 口径：回看几个筛选日、至少命中几次、成交额门槛、上次扫描扫了多少只；
        ② 候选进池 + 池内现状：`candidates.build` 已经算好的证据清单（页面只读，不自己排序）；
        ③ 形态表现：每种形态命中后 5/20 日的实际表现（要回填过才有数）。

        这里不做任何新计算——都是把已有的结果凑到一处，页面不发明口径。
        """
        from . import candidates as candidates_mod, screen as screen_mod, universe as universe_mod

        now = time.time()
        if not force and self._pool_cache and now - self._pool_cache[0] < self.POOL_TTL:
            return self._pool_cache[1]

        conn = db.connect(self.cfg["_db_path"])
        try:
            payload = dict(candidates_mod.build(conn, self.cfg))
            run = db.query_one(
                conn,
                "SELECT run_date, rows, error_msg FROM data_health WHERE task='screen' "
                "ORDER BY run_date DESC LIMIT 1",
            )
            payload["last_run"] = (
                {"trade_date": run["run_date"], "scanned": run["rows"], "note": run["error_msg"]}
                if run else None
            )
            payload["min_avg_amount"] = universe_mod.min_avg_amount(self.cfg)
            payload["outcomes"] = screen_mod.outcome_stats(conn)
            # 按交易日聚合 + 多重检验校正：8 个形态里最好的那个，t 值本身就被挑过一遍
            payload["pattern_stats"] = screen_mod.pattern_stats(conn, self.cfg)
            self._pool_cache = (now, payload)
            return payload
        except Exception as exc:            # 页面永远不该白屏
            return {"ok": False, "message": f"筛选池读不出来：{type(exc).__name__} {exc}"}
        finally:
            conn.close()

    def job_state(self) -> dict:
        return self.jobs.state()

    def run_job(self, key: str, code: str = "", scope: str = "") -> dict:
        """页面按钮统一走这里：同一时刻只允许一个任务。

        `code` / `scope` 只有 AI 分析用得到：单只看 code，台账与回测看 scope。
        """
        cfg = self.cfg
        self._pool_cache = None      # 任务一开跑就作废缓存，跑完刷新能看到新数据
        self._newstock_cache = None

        def daily():
            conn = db.connect(cfg["_db_path"])
            try:
                summary = tasks.run_daily(conn, cfg, verbose=True)
                print(f"交易日 {summary['trade_date']}，提醒 {len(summary['alerts'])} 条")
                return f"完成：{summary['trade_date']}"
            finally:
                conn.close()

        def sync():
            conn = db.connect(cfg["_db_path"])
            try:
                warehouse_mod.sync_universe(conn, cfg)
                result = warehouse_mod.sync_history(conn, cfg)
                if not result.get("ok"):
                    raise RuntimeError(result.get("message", "同步失败"))
                return (f"本批补 {result['written']} 只，还剩 {result['remaining']} 只"
                        if result["remaining"] else "全部标的都已跟到最新交易日")
            finally:
                conn.close()

        def screen():
            from . import screen as screen_mod

            conn = db.connect(cfg["_db_path"])
            try:
                result = screen_mod.scan(conn, cfg)
                if not result.get("ok"):
                    raise RuntimeError(result.get("message", "筛选失败"))
                screen_mod.sync_criteria(conn, cfg, result.get("trade_date"))
                saved = screen_mod.save(conn, result)
                return f"扫描 {result['with_data']} 只，入库 {saved} 条"
            finally:
                conn.close()

        def snapshot():
            conn = db.connect(cfg["_db_path"])
            try:
                result = warehouse_mod.snapshot_bars(conn, cfg)
                if not result.get("ok"):
                    raise RuntimeError(result.get("message", "快照失败"))
                return f"写入 {result['written']} 只当日 K 线"
            finally:
                conn.close()

        def intraday():
            conn = db.connect(cfg["_db_path"])
            try:
                info = tasks.collect_intraday_bars(conn, cfg, verbose=True)
                if not info.get("ok"):
                    raise RuntimeError(info.get("message", "分时采集失败"))
                return f"当日分时：{info['codes']} 只、{info['bars']} 个点"
            finally:
                conn.close()

        def push():
            conn = db.connect(cfg["_db_path"])
            try:
                result = notify.push_daily(conn, cfg)
                if not result.get("ok"):
                    raise RuntimeError(result.get("note") or "推送失败")
                if result.get("skipped"):
                    return result.get("note") or "已跳过"
                return f"已推到手机：{result['title']}"
            finally:
                conn.close()

        def backtest():
            from . import backtest as backtest_mod

            conn = db.connect(cfg["_db_path"])
            try:
                result = backtest_mod.run_grid(conn, cfg, verbose=True)
                if not result.get("ok"):
                    raise RuntimeError(result.get("message", "回测失败"))
                # 换手率研究跟参数回测一起跑：它回答的是"放量之后怎么了"，
                # 和"哪组参数行"是两个问题，但用的是同一批本地数据和同一把尺子。
                study = backtest_mod.turnover_study(conn, cfg, verbose=False)
                if study.get("ok"):
                    backtest_mod.save_turnover(study, cfg["_project_root"])
                backtest_mod.write_report(backtest_mod.render_report(result), cfg["_project_root"])
                saved = backtest_mod.save_result(result, cfg["_project_root"])
                return (f"扫了 {len(result['results'])} 组参数，另做了换手率研究，"
                        f"页面结果已更新（{saved.name}）")
            finally:
                conn.close()

        def analyze():
            from . import analysis as analysis_mod

            target_scope = scope or analysis_mod.SCOPE_INSTRUMENT
            conn = db.connect(cfg["_db_path"])
            try:
                if target_scope == analysis_mod.SCOPE_INSTRUMENT:
                    target = code or next((item["code"] for item in watchlist_codes(cfg)), "")
                    if not target:
                        raise RuntimeError("没指定标的，观察池也是空的")
                else:
                    target = ""
                result = analysis_mod.run(conn, cfg, target_scope, code=target)
                if not result.get("ok"):
                    raise RuntimeError(result.get("note") or "分析失败")
                usage = result.get("usage") or {}
                label = target or ("因子台账" if target_scope == analysis_mod.SCOPE_LEDGER else "回测摘要")
                return (f"{label} 分析完成（{result['model']}，"
                        f"{usage.get('prompt_tokens')}+{usage.get('completion_tokens')} tokens）")
            finally:
                conn.close()

        def listings():
            from . import newstock as newstock_mod

            conn = db.connect(cfg["_db_path"])
            try:
                result = newstock_mod.refresh(conn, cfg, verbose=True)
                return f"新股 {result['new']} 只、次新 {result['recent']} 只（判定日 {result['as_of'] or '—'}）"
            finally:
                conn.close()

        table = {
            "daily": ("更新自选数据", daily),
            "sync": ("同步全市场", sync),
            "screen": ("跑全市场筛选", screen),
            "snapshot": ("补当日快照", snapshot),
            "intraday": ("抓当日分时", intraday),
            "push": ("推送简报到手机", push),
            "backtest": ("跑参数回测", backtest),
            "analyze": ("AI 分析这只标的", analyze),
            "listings": ("刷新新股与次新", listings),
        }
        entry = table.get(key)
        if entry is None:
            return {"ok": False, "message": f"未知任务：{key}"}
        return self.jobs.start(key, entry[0], entry[1])

    # ---- 自选清单 ----

    def watchlist(self) -> list[dict]:
        conn = db.connect(self.cfg["_db_path"])
        try:
            items: list[dict] = []
            for item in watchlist_codes(self.cfg):
                code = item["code"]
                info = db.query_one(conn, "SELECT name FROM instruments WHERE code=?", (code,))
                bar = db.query_one(
                    conn,
                    "SELECT trade_date, close, pct_chg FROM bars_daily WHERE code=? ORDER BY trade_date DESC LIMIT 1",
                    (code,),
                )
                feat = db.query_one(
                    conn, "SELECT * FROM features_daily WHERE code=? ORDER BY trade_date DESC LIMIT 1", (code,)
                )
                bars = db.query_one(conn, "SELECT COUNT(*) AS n FROM bars_daily WHERE code=?", (code,))
                items.append(
                    {
                        "code": code,
                        "name": display_name(code, info["name"] if info else "") or code,
                        "type": item["type"],
                        "role": item["role"],
                        "bars": bars["n"] if bars else 0,
                        "last_date": bar["trade_date"] if bar else None,
                        "close": bar["close"] if bar else None,
                        "pct_chg": bar["pct_chg"] if bar else None,
                        "state": feat["state"] if feat else None,
                        "state_days": feat["state_days"] if feat else None,
                        "trend_score": feat["trend_score"] if feat else None,
                        "opportunity_score": feat["opportunity_score"] if feat else None,
                    }
                )
            return items
        finally:
            conn.close()

    # ---- 单只标的的图与指标 ----

    def intraday(self, code: str) -> dict:
        """当日分时线。本地没有就现抓一次——分时接口只给当天，按需抓最省事。

        返回的点里带累计均价（成交额 ÷ 成交量），页面画的就是"价格线 + 均价线"。
        """
        conn = db.connect(self.cfg["_db_path"])
        try:
            day = _latest_intraday_day(conn, code)
            if not day:
                tasks.collect_intraday_bars(conn, self.cfg, [code], verbose=False)
                day = _latest_intraday_day(conn, code)
            if not day:
                return {"code": code, "date": None, "points": [], "prev_close": None,
                        "message": "还没有分时数据：盘中跑一次盘中任务，或双击 0-安装环境 补齐数据源"}

            points: list[dict] = []
            cum_volume = cum_amount = 0.0
            for row in _intraday_rows(conn, code, day):
                volume = float(row["volume"] or 0)
                cum_volume += volume
                cum_amount += float(row["amount"] or 0)
                points.append({
                    "t": str(row["dt"])[11:16],
                    "p": row["close"],
                    "v": volume,
                    "avg": round(cum_amount / cum_volume, 4) if cum_volume else row["close"],
                })
            return {
                "code": code,
                "date": day,
                "points": points,
                "prev_close": _prev_close(conn, code, day),
                "source": points and "bars_intraday" or None,
            }
        finally:
            conn.close()

    def kline(self, code: str, days: int = 250) -> dict:
        days = max(30, min(int(days or 250), 2000))
        conn = db.connect(self.cfg["_db_path"])
        try:
            info_row = db.query_one(conn, "SELECT name, type FROM instruments WHERE code=?", (code,))
            info = dict(info_row) if info_row else {}
            bars = [
                dict(row)
                for row in db.query(
                    conn,
                    """SELECT trade_date, open, high, low, close, volume, amount, pct_chg, quality_flag
                       FROM bars_daily WHERE code=? ORDER BY trade_date DESC LIMIT ?""",
                    (code, days),
                )
            ]
            bars.reverse()
            feats = {
                row["trade_date"]: dict(row)
                for row in db.query(
                    conn,
                    """SELECT trade_date, ma20, ma60, ma120, trend_score, state, state_days,
                              opportunity_score, vol_ratio_20, breakout_confirmed, consolidation_days,
                              position_cap, stop_level, risk_reward, atr_pct,
                              candle_pattern, risk_note
                       FROM features_daily WHERE code=? ORDER BY trade_date DESC LIMIT ?""",
                    (code, days),
                )
            }
            for bar in bars:
                feat = feats.get(bar["trade_date"], {})
                bar.update(
                    {
                        "ma20": feat.get("ma20"),
                        "ma60": feat.get("ma60"),
                        "ma120": feat.get("ma120"),
                        "trend_score": feat.get("trend_score"),
                        "state": feat.get("state"),
                        "state_days": feat.get("state_days"),
                        "opportunity_score": feat.get("opportunity_score"),
                        "vol_ratio_20": feat.get("vol_ratio_20"),
                        "breakout_confirmed": feat.get("breakout_confirmed"),
                        "consolidation_days": feat.get("consolidation_days"),
                        "position_cap": feat.get("position_cap"),
                        "stop_level": feat.get("stop_level"),
                        "risk_reward": feat.get("risk_reward"),
                        "atr_pct": feat.get("atr_pct"),
                        "candle_pattern": feat.get("candle_pattern"),
                        "risk_note": feat.get("risk_note"),
                    }
                )

            levels = [
                dict(row)
                for row in db.query(
                    conn,
        """SELECT level_type, price_low, price_high, weight, engine FROM levels
                       WHERE code=? AND trade_date=(SELECT MAX(trade_date) FROM levels WHERE code=?)
                       ORDER BY price_low""",
                    (code, code),
                )
            ]
            # K 线形态标记：只给"关键位置 + 明显形态"的那些（口径在 candles.marks 里定死）。
            # 每天画形态等于没画——250 根 K 线里每根都能被叫做"十字星"或"长阳"。
            marks = {item["trade_date"]: item for item in candles_mod.marks(bars)}
            for bar in bars:
                mark = marks.get(bar["trade_date"])
                if mark:
                    bar["candle"] = {
                        "pattern": mark["pattern"],
                        "up": mark["up"],
                        "down": mark["down"],
                        "combo": mark.get("combo", False),
                        "reasons": mark["reasons"],
                    }
            alerts = [
                dict(row)
                for row in db.query(
                    conn,
                    """SELECT trade_date, level, signal_type, message, price FROM alerts
                       WHERE code=? ORDER BY trade_date DESC, level LIMIT 40""",
                    (code,),
                )
            ]
            factor_row = db.query_one(
                conn, "SELECT MAX(trade_date) AS d FROM factor_contributions WHERE code=?", (code,)
            )
            factor_date = dict(factor_row) if factor_row else {}
            factors: list[dict] = []
            if factor_date.get("d"):
                factors = [
                    dict(row)
                    for row in db.query(
                        conn,
                        """SELECT fc.factor_id, fr.name, fr.category, fr.role, fr.status,
                                  fc.raw_value, fc.normalized_score, fc.weight, fc.contribution
                           FROM factor_contributions fc
                           LEFT JOIN factor_registry fr ON fr.factor_id = fc.factor_id
                           WHERE fc.code=? AND fc.trade_date=?
                           ORDER BY fc.contribution DESC""",
                        (code, factor_date["d"]),
                    )
                ]
            return {
                "code": code,
                "name": display_name(code, info.get("name")) or code,
                "type": info.get("type"),
                "days": days,
                "factor_date": factor_date.get("d"),
                "bars": bars,
                "levels": levels,
                "alerts": alerts,
                "factors": factors,
            }
        finally:
            conn.close()

    def recent_alerts(self, limit: int = 30) -> list[dict]:
        conn = db.connect(self.cfg["_db_path"])
        try:
            codes = [item["code"] for item in watchlist_codes(self.cfg)]
            if not codes:
                return []
            marks = ",".join("?" for _ in codes)
            rows = db.query(
                conn,
                f"""SELECT a.trade_date, a.code, a.level, a.signal_type, a.message,
                           COALESCE(i.name, a.code) AS name
                    FROM alerts a LEFT JOIN instruments i ON i.code = a.code
                    WHERE a.code IN ({marks}) ORDER BY a.trade_date DESC, a.level, a.code LIMIT ?""",
                (*codes, limit),
            )
            return [
                {**dict(row), "name": display_name(row["code"], row["name"]) or row["code"]}
                for row in rows
            ]
        finally:
            conn.close()

    # ---- 搜索与自选维护 ----

    def _ensure_directory(self, conn) -> int:
        """把全市场代码表缓存进 instruments（只在需要按名字搜时才联网拉一次）。"""
        row = db.query_one(conn, "SELECT COUNT(*) AS n FROM instruments WHERE in_watchlist=0")
        cached = row["n"] if row else 0
        if cached >= 3000:
            return cached
        try:
            source = build_source("akshare", self.cfg)
            rows = source.symbol_directory()
        except Exception:
            return cached
        if not rows:
            return cached
        payload = [
            {
                "code": item["code"],
                "name": item["name"],
                "type": item.get("type") or classify_symbol(item["code"]),
                "in_watchlist": 0,
                "updated_at": db.now_iso(),
            }
            for item in rows
        ]
        db.upsert_rows(conn, "instruments", payload, ["code"])
        return len(payload)

    def search(self, query: str) -> list[dict]:
        text = str(query or "").strip()
        if not text:
            return []
        conn = db.connect(self.cfg["_db_path"])
        try:
            self._ensure_directory(conn)
            like = f"%{text}%"
            rows = db.query(
                conn,
                """SELECT code, name, type FROM instruments
                   WHERE code LIKE ? OR name LIKE ?
                   ORDER BY length(name), code LIMIT 30""",
                (like, like),
            )
            in_watch = {item["code"] for item in watchlist_codes(self.cfg)}
            return [{**dict(row), "in_watchlist": row["code"] in in_watch} for row in rows]
        finally:
            conn.close()

    def add(self, raw_code: str, kind: str | None = None) -> dict:
        code = normalize_code(raw_code)
        if not code:
            return {"ok": False, "message": "代码没认出来，试试 600519 或 SH600519 这种写法"}
        kind = kind if kind in WATCHLIST_KEYS else guess_kind(code)
        key = WATCHLIST_KEYS[kind]
        existing = {item["code"] for item in watchlist_codes(self.cfg)}
        if code in existing:
            return {"ok": False, "message": f"{code} 已经在自选里了"}

        cfg_path = Path(self.cfg["_config_path"])
        with self.lock:
            self.cfg["watchlist"].setdefault(key, [])
            self.cfg["watchlist"][key].append(code)
            _rewrite_watchlist(cfg_path, self.cfg)
            try:
                conn = db.connect(self.cfg["_db_path"])
                try:
                    result = tasks.collect_instrument(conn, self.cfg, code, kind, verbose=False)
                finally:
                    conn.close()
            except Exception as exc:                  # 取数或落库炸了也要回滚自选
                result = {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
            if not result.get("ok"):
                # 取不到数据就把自选回滚，不留空壳
                self.cfg["watchlist"][key].remove(code)
                _rewrite_watchlist(cfg_path, self.cfg)
                return result
            return {"ok": True, "code": code, "kind": kind, **result}

    def remove(self, raw_code: str) -> dict:
        code = normalize_code(raw_code) or str(raw_code or "").strip().upper()
        cfg_path = Path(self.cfg["_config_path"])
        with self.lock:
            for key in ("indices", "etfs", "stocks"):
                values = self.cfg["watchlist"].get(key) or []
                if code not in values:
                    continue
                values.remove(code)
                _rewrite_watchlist(cfg_path, self.cfg)
                conn = db.connect(self.cfg["_db_path"])
                try:
                    conn.execute("UPDATE instruments SET in_watchlist=0 WHERE code=?", (code,))
                    conn.commit()
                finally:
                    conn.close()
                return {"ok": True, "code": code}
        return {"ok": False, "message": f"{code} 不在自选里"}


# ---------- 路由（和 HTTP 收发分开，方便离线测） ----------

JSON_TYPE = "application/json; charset=utf-8"


def _screen_payload(cfg: dict) -> dict:
    """全市场筛选结果（由 20-全市场筛选 生成，页面只读）。"""
    from . import screen as screen_mod

    conn = db.connect(cfg["_db_path"])
    try:
        return screen_mod.load(conn, cfg)
    finally:
        conn.close()


def _coverage(cfg: dict) -> dict:
    """本地仓库覆盖情况，给页面一个上下文（一眼知道能筛多少票）。"""
    from . import warehouse as warehouse_mod

    conn = db.connect(cfg["_db_path"])
    try:
        return warehouse_mod.status(conn)
    finally:
        conn.close()


def _meta_payload(cfg: dict) -> dict:
    """页面用的口径说明：阈值和参数都从配置读，改了配置说明也跟着变。"""
    state = cfg.get("state") or {}
    risk = cfg.get("risk") or {}
    return {
        "state": {key: state.get(key) for key in
                  ("enter_up", "exit_up", "enter_down", "exit_down", "confirm_days",
                   "min_state_days", "neutral_band")},
        "risk": {key: risk.get(key) for key in
                 ("base_cap", "target_atr_pct", "stop_buffer_pct", "atr_stop_multiple",
                  "max_stop_pct", "win_rate", "target_expectancy",
                  "open_space_high_tolerance_pct", "open_space_atr_multiple",
                  "no_resistance_rr", "cap_floor", "turnover_discount", "structure_stop")},
        "alerts": cfg.get("alerts") or {},
        "feature_version": (cfg.get("project") or {}).get("feature_version"),
        "factors": [
            {
                "factor_id": item.get("factor_id"),
                "name": item.get("name") or item.get("factor_id"),
                "category": item.get("category"),
                "role": item.get("role"),
                "weight": item.get("weight"),
                "status": item.get("status"),
            }
            for item in (cfg.get("factors") or [])
        ],
    }


def _health_payload(cfg: dict) -> dict:
    """页面用来自检"服务是不是旧进程"。

    Python 模块只在进程启动时加载一次，页面 HTML 却是每次请求都重新读——
    所以改了后端之后不重启服务，页面会看起来"时好时坏"，很难查。
    这里把进程启动时间和关键文件的修改时间都报出来，一眼能看出是不是该重启。
    """
    modules = Path(__file__).resolve().parent
    newest = max(
        (path.stat().st_mtime for path in modules.rglob("*.py")),
        default=0,
    )
    return {
        "process_started_at": PROCESS_STARTED_AT,
        "code_mtime": datetime.fromtimestamp(newest).strftime("%Y-%m-%d %H:%M:%S"),
        "stale": newest > PROCESS_STARTED_TS,
        "routes": ["/api/watchlist", "/api/kline", "/api/intraday", "/api/quotes", "/api/freshness",
                   "/api/meta", "/api/market", "/api/screen", "/api/alerts", "/api/search",
                   "/api/jobs", "/api/factors", "/api/backtest", "/api/analysis", "/api/pool",
                   "/api/newstock", "/api/support"],
    }


def _market_payload(cfg: dict, trade_date: str | None = None) -> dict:
    """全市场概览：直接用本地仓库算，不联网。

    样本量必须一起返回——本地只补了 40 只的时候，"涨跌家数"没有代表性，
    页面得把这个前提显示出来，不能让人误以为是全市场。
    """
    conn = db.connect(cfg["_db_path"])
    try:
        if trade_date is None:
            row = db.query_one(conn, "SELECT MAX(trade_date) AS d FROM bars_daily")
            trade_date = row["d"] if row else None
        if not trade_date:
            return {"trade_date": None, "sample": 0, "note": "本地还没有日线数据"}

        # 只数个股：指数不是"家"，而且指数那列的成交额是整个市场的量（上证指数 ≈ 全市场成交额），
        # 混进来会把"全市场成交额"直接放大成几万亿的假数字。口径和 breadth.local_rows 一致。
        rows = [
            dict(row)
            for row in db.query(
                conn,
                """SELECT b.code, COALESCE(i.name, b.code) AS name, b.close, b.pct_chg, b.amount
                   FROM bars_daily b LEFT JOIN instruments i ON i.code = b.code
                   WHERE b.trade_date=? AND b.pct_chg IS NOT NULL
                     AND (i.type IS NULL OR i.type = 'stock')""",
                (trade_date,),
            )
        ]
        for row in rows:
            row["name"] = display_name(row["code"], row["name"]) or row["code"]

        pcts = sorted(row["pct_chg"] for row in rows)
        sample = len(pcts)
        median = pcts[sample // 2] if sample else None
        buckets = [("跌超 7%", -100, -7), ("跌 3~7%", -7, -3), ("跌 0~3%", -3, 0),
                   ("涨 0~3%", 0, 3), ("涨 3~7%", 3, 7), ("涨超 7%", 7, 100)]
        distribution = [
            {"label": label, "count": sum(1 for value in pcts if low <= value < high)}
            for label, low, high in buckets
        ]
        ranked = sorted(rows, key=lambda item: item["pct_chg"], reverse=True)
        brief = lambda item: {key: item[key] for key in ("code", "name", "close", "pct_chg")}
        return {
            "trade_date": trade_date,
            "sample": sample,
            "coverage": _coverage(cfg),
            "breadth": {
                "up": sum(1 for value in pcts if value > 0),
                "down": sum(1 for value in pcts if value < 0),
                "flat": sum(1 for value in pcts if value == 0),
                "limit_up": sum(1 for value in pcts if value >= 9.8),
                "limit_down": sum(1 for value in pcts if value <= -9.8),
                "median_pct": round(median, 2) if median is not None else None,
                "total_amount": round(sum(row["amount"] or 0 for row in rows), 0),
            },
            "distribution": distribution,
            "gainers": [brief(item) for item in ranked[:10]],
            "losers": [brief(item) for item in ranked[-10:][::-1]],
            # 资金去向：钱在宽基 / 行业主题 ETF / 成交额 / 融资余额之间怎么走。
            # 和"涨跌家数"放在同一页是对的——它们回答的是同一个问题：
            # 今天这波涨跌背后，钱是进来还是出去、往哪个方向挪。
            "flow": flow_mod.snapshot(conn, cfg, trade_date),
        }
    finally:
        conn.close()


def _payload_bytes(payload, status: int = 200) -> tuple[int, str, bytes]:
    return status, JSON_TYPE, json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")


def dispatch(app: App, method: str, raw_path: str, body: bytes = b"") -> tuple[int, str, bytes]:
    """把一次请求变成一次取数。返回 (状态码, Content-Type, 响应体)。"""
    parsed = urlparse(raw_path)
    query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
    path = parsed.path
    try:
        if method == "GET":
            if path in ("/", "/index.html"):
                return 200, "text/html; charset=utf-8", INDEX_FILE.read_bytes()
            if path == "/api/watchlist":
                return _payload_bytes({"items": app.watchlist(), "coverage": _coverage(app.cfg)})
            if path == "/api/kline":
                return _payload_bytes(app.kline(query.get("code", ""), query.get("days", 250)))
            if path == "/api/intraday":
                return _payload_bytes(app.intraday(query.get("code", "")))
            if path == "/api/quotes":
                codes = [c for c in (query.get("codes", "") or "").split(",") if c.strip()]
                return _payload_bytes(app.quotes(codes))
            if path == "/api/freshness":
                return _payload_bytes(app.freshness())
            if path == "/api/alerts":
                return _payload_bytes({"items": app.recent_alerts()})
            if path == "/api/screen":
                return _payload_bytes(_screen_payload(app.cfg))
            if path == "/api/factors":
                return _payload_bytes(app.factors())
            if path == "/api/backtest":
                return _payload_bytes(app.backtest())
            if path == "/api/analysis":
                return _payload_bytes(app.analysis(query.get("code", ""), query.get("scope", "")))
            if path == "/api/pool":
                return _payload_bytes(app.pool())
            if path == "/api/newstock":
                return _payload_bytes(app.newstock())
            if path == "/api/support":
                return _payload_bytes(app.support())
            if path == "/api/meta":
                return _payload_bytes(_meta_payload(app.cfg))
            if path == "/api/market":
                return _payload_bytes(_market_payload(app.cfg, query.get("date")))
            if path == "/api/jobs":
                return _payload_bytes(app.job_state())
            if path == "/api/health":
                return _payload_bytes(_health_payload(app.cfg))
            if path == "/api/search":
                return _payload_bytes({"items": app.search(query.get("q", ""))})
        elif method == "POST" and path == "/api/watchlist":
            try:
                payload = json.loads(body or b"{}")
            except Exception:
                payload = {}
            action = payload.get("action")
            if action == "add":
                return _payload_bytes(app.add(payload.get("code", ""), payload.get("kind")))
            if action == "remove":
                return _payload_bytes(app.remove(payload.get("code", "")))
            return _payload_bytes({"ok": False, "message": f"未知操作：{action}"}, 400)
        elif method == "POST" and path == "/api/jobs":
            try:
                payload = json.loads(body or b"{}")
            except Exception:
                payload = {}
            return _payload_bytes(app.run_job(
                payload.get("job", ""), payload.get("code", ""), payload.get("scope", "")
            ))
        return _payload_bytes({"error": "接口不存在"}, 404)
    except Exception as exc:                      # 让页面能显示错误，而不是白屏
        return _payload_bytes({"error": f"{type(exc).__name__}: {exc}"}, 500)


class Handler(BaseHTTPRequestHandler):
    app: App = None                     # 由 serve() 注入

    def log_message(self, *args):       # 控制台不要刷访问日志
        pass

    def _respond(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._respond(*dispatch(self.app, "GET", self.path))

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self._respond(*dispatch(self.app, "POST", self.path, body))


def serve(cfg: dict, port: int = 8765, open_browser: bool = True) -> int:
    if not INDEX_FILE.exists():
        print(f"找不到页面文件：{INDEX_FILE}")
        return 1
    app = App(cfg)
    handler = type("BoundHandler", (Handler,), {"app": app})

    server = None
    last_error = ""
    for candidate in range(port, port + 6):           # 端口被占就往后试
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
            break
        except OSError as exc:
            last_error = f"{candidate}: {exc}"
            continue
    if server is None:
        print(f"起不了服务（试了 {port}~{port + 5}）：{last_error}")
        print("端口被占用就换一个：python run.py web --port 9000")
        print("如果提示 Operation not permitted，说明当前环境不允许监听端口，换到本机终端里跑。")
        return 1

    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"看盘页面已启动：{url}")
    print("（这个窗口关掉 = 停止服务；页面里加的标的会写回 config.yaml）")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止服务。")
    finally:
        server.server_close()
    return 0
