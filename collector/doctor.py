"""环境体检：Python、依赖、数据库、配置，一次看全。

为什么需要它：启动器只保证"能跑起来"（PyYAML 在就行），剩下的问题都要跑到一半才暴露——
库文件被人删了、库是旧版本缺表、可选的数据源没装、token 没配。这些都能提前查出来，
而且都能用一句人话讲清楚，不该让用户对着 traceback 猜。

体检**只读**：不建库、不装包、不改配置。要修的话它会把命令写在结论里。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from . import db

# 核心链路必需 / 可选（可选缺失只是少几个数据源，不影响主流程）
REQUIRED_MODULES = [("yaml", "PyYAML", "配置解析，唯一硬依赖")]
OPTIONAL_MODULES = [
    ("requests", "requests", "腾讯/新浪日线"),
    ("baostock", "baostock", "备份源（换手率只有它给）"),
    ("akshare", "akshare", "兜底源"),
    ("adata", "adata", "低频补充源"),
]


def module_version(name: str) -> str | None:
    try:
        module = __import__(name)
    except Exception:
        return None
    return getattr(module, "__version__", "已安装")


def check_python() -> dict:
    return {
        "ok": sys.version_info >= (3, 10),
        "executable": sys.executable,
        "version": sys.version.split()[0],
        "note": "" if sys.version_info >= (3, 10) else "需要 Python 3.10 以上",
    }


def check_modules() -> dict:
    required = [
        {"module": mod, "name": label, "why": why, "version": module_version(mod)}
        for mod, label, why in REQUIRED_MODULES
    ]
    optional = [
        {"module": mod, "name": label, "why": why, "version": module_version(mod)}
        for mod, label, why in OPTIONAL_MODULES
    ]
    return {
        "ok": all(item["version"] for item in required),
        "required": required,
        "optional": optional,
        "missing_optional": [item["name"] for item in optional if not item["version"]],
    }


def check_database(db_path: str | Path, schema_path: str | Path) -> dict:
    """库文件本身健不健康：在不在、能不能开、完整性、表齐不齐、有没有数据。"""
    path = Path(db_path)
    report: dict = {
        "path": str(path),
        "exists": path.exists(),
        "size_mb": round(path.stat().st_size / 1e6, 1) if path.exists() else 0.0,
        "ok": False,
        "missing_tables": [],
        "tables": 0,
        "rows": {},
        "latest_bar": None,
        "note": "",
    }
    if not path.exists():
        report["note"] = "还没有数据库，跑一次 init-db 就会建（空的，之后跑 3-每日任务 才有数据）"
        return report

    expected = [
        line.split("IF NOT EXISTS", 1)[1].split("(")[0].strip()
        for line in Path(schema_path).read_text(encoding="utf-8").splitlines()
        if line.strip().upper().startswith("CREATE TABLE IF NOT EXISTS")
    ]
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        report["integrity"] = conn.execute("PRAGMA quick_check").fetchone()[0]
        report["journal_mode"] = conn.execute("PRAGMA journal_mode").fetchone()[0]
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        report["tables"] = len(tables)
        report["missing_tables"] = sorted(set(expected) - tables)
        for table in ("instruments", "bars_daily", "features_daily", "alerts", "new_listings"):
            if table in tables:
                report["rows"][table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if "bars_daily" in tables:
            row = conn.execute("SELECT MAX(trade_date) AS d FROM bars_daily").fetchone()
            report["latest_bar"] = row["d"] if row else None
        # 非交易日不该有行情。踩过：数据源失败时兜底成了"今天"，周六的日终把周五的
        # 快照贴上周六的日期写了进来——多出来的那根假 K 线肉眼看不出四价有什么问题，
        # 但均线、形态、回放全跟着错。这里专门盯它。
        if "bars_daily" in tables and "trade_calendar" in tables:
            report["off_calendar"] = [
                row["trade_date"]
                for row in conn.execute(
                    """SELECT DISTINCT b.trade_date
                         FROM bars_daily b
                         JOIN trade_calendar t ON t.trade_date = b.trade_date
                        WHERE COALESCE(t.is_trading_day, 1) = 0
                        ORDER BY b.trade_date DESC"""
                )
            ]
        conn.close()
    except sqlite3.DatabaseError as exc:
        report["note"] = f"库打不开：{exc}（可能损坏，或正被别的工具锁着）"
        return report

    problems = []
    if report.get("integrity") != "ok":
        problems.append(f"完整性检查没通过（{report.get('integrity')}）")
    if report["missing_tables"]:
        problems.append(f"缺 {len(report['missing_tables'])} 张表（跑一次 init-db 会自动补）")
    if not report["rows"].get("bars_daily"):
        problems.append("日线表是空的（跑一次 3-每日任务）")
    if report.get("off_calendar"):
        days = "、".join(report["off_calendar"][:3])
        problems.append(f"{len(report['off_calendar'])} 个非交易日却有行情（{days}）——"
                        "多半是数据源失败时把上一交易日的快照贴到了今天，先删掉再重跑")
    report["ok"] = not problems
    report["note"] = "；".join(problems)
    return report


# ---------------------------------------------------------------- 数据源
#
# 两层校验，分开跑：
#   · 装没装（is_available，纯本地 import 检查）——每次体检都查，零成本；
#   · 通不通（真取一次沪深300日线）——只在 --sources 时查，要联网、10~30 秒。
# 必须分两层的原因：包装了但接口不通是常态（本机网络上 akshare 的东财接口直接断），
# 只看"装没装"会给出虚假的安心。

# 能力 → 它能拿来干什么。体检不只报"哪个源挂了"，还要报"因此哪件事做不了"：
# akshare 走东财、在这条网络上连不上，真正受损的不是"akshare"这个包，
# 而是只有它提供的那几个能力（ETF 份额、龙虎榜、融资融券）。
CAPABILITY_USES = {
    "daily_bars": "日线（每日任务、全市场同步、回测）",
    "trade_calendar": "交易日历",
    "market_snapshot": "全市场快照（每日增量）",
    "intraday_snapshot": "实时报价（盯盘条）",
    "intraday_bars": "分时线",
    "symbol_directory": "全市场代码表",
    "stock_basic": "上市日（新股与次新的判定）",
    "etf_shares": "ETF 份额（etf_share 影子因子）",
    "lhb": "龙虎榜（lhb 表）",
    "margin": "融资融券（margin 表）",
}


def check_sources(cfg: dict, probe: bool = False) -> dict:
    from .sources import SOURCE_REGISTRY, build_source

    conf = cfg.get("sources") or {}
    names = [name for name in SOURCE_REGISTRY if name not in ("fixture", "fixture_alt", "csv")]
    items: list[dict] = []
    for name in names:
        item = {"name": name, "installed": False, "ok": None, "note": "",
                "bars": 0, "capabilities": []}
        try:
            source = build_source(name, cfg)
            item["capabilities"] = sorted(getattr(source, "capabilities", set()) or [])
            ok, note = source.is_available()
            item["installed"] = bool(ok)
            item["note"] = "" if ok else str(note)
        except Exception as exc:
            item["note"] = f"{type(exc).__name__} {exc}"[:120]
        items.append(item)

    if probe:
        by_name = {item["name"]: item for item in items}
        for probed in probe_sources(cfg):
            target = by_name.get(probed["name"])
            if target is not None:
                target.update({k: v for k, v in probed.items() if k != "name"})

    usable = [item["name"] for item in items
              if item["installed"] and (item["ok"] is not False)]

    # 能力覆盖：每个能力现在有哪些可用的源在提供；一个都没有的，就是"这件事现在做不了"
    coverage: dict[str, list[str]] = {}
    for item in items:
        if item["name"] not in usable:
            continue
        for capability in item["capabilities"]:
            coverage.setdefault(capability, []).append(item["name"])
    gaps = {capability: CAPABILITY_USES.get(capability, "")
            for capability in CAPABILITY_USES if capability not in coverage}

    return {
        "probed": bool(probe),
        "configured": {key: conf.get(key) for key in
                       ("primary", "backup", "fallback", "extra", "directory")},
        "items": items,
        "usable": usable,
        "coverage": coverage,
        "gaps": gaps,
        "note": "" if usable else "一个数据源都用不了：先双击 0-安装环境，再看网络",
    }


def probe_sources(cfg: dict, days: int = 40) -> list[dict]:
    """真的取一次数：拿沪深300当探针，回答"这个接口现在通不通"。

    只看"包装没装"没有意义——真正的坑是包在、但接口连不上。
    """
    from datetime import datetime, timedelta

    from .sources import SOURCE_REGISTRY, build_source

    today = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    out: list[dict] = []
    for name in SOURCE_REGISTRY:
        if name in ("fixture", "fixture_alt", "csv"):
            continue
        item = {"name": name, "installed": False, "ok": False, "note": "", "bars": 0}
        try:
            source = build_source(name, cfg)
            ok, note = source.is_available()
            item["installed"] = bool(ok)
            if not ok:
                item["note"] = str(note)
                out.append(item)
                continue
            if "daily_bars" not in getattr(source, "capabilities", set()):
                item["note"] = "这个源不支持日线接口"
                out.append(item)
                continue
            rows = source.daily_bars("SH000300", start, today, "index")
            item["ok"] = bool(rows)
            item["bars"] = len(rows)
            item["note"] = f"真取到 {len(rows)} 根日线" if rows else "取数返回空"
        except Exception as exc:
            item["note"] = f"{type(exc).__name__} {str(exc)[:110]}"
        out.append(item)
    return out


def check_config(cfg: dict) -> dict:
    """配置能不能用，以及敏感信息配好了没有（只说配没配，绝不打印内容）。"""
    from .config import watchlist_codes
    from . import analysis as analysis_mod, notify as notify_mod

    items = watchlist_codes(cfg)
    push_key, push_src = notify_mod.resolve_token(cfg)
    ai_key, ai_src = analysis_mod.resolve_key(cfg)
    return {
        "ok": bool(items),
        "watchlist": len(items),
        "watchlist_codes": [item["code"] for item in items],
        "push": {"enabled": notify_mod.settings(cfg)["enabled"], "configured": bool(push_key), "from": push_src},
        "ai": {"enabled": analysis_mod.settings(cfg)["enabled"], "configured": bool(ai_key), "from": ai_src},
        "note": "" if items else "观察池是空的：config.yaml 的 watchlist 里先加几只",
    }


def report(cfg: dict, db_path: str | None = None, probe: bool = False) -> dict:
    """体检总报告。只读，不修任何东西。

    probe=True 时会对每个数据源**真取一次数**（要联网，10~30 秒）——
    默认关着，因为体检也会在自动化任务里跑，不该每次都等半分钟。
    """
    schema = Path(cfg["_project_root"]) / "schema.sql"
    return {
        "python": check_python(),
        "modules": check_modules(),
        "sources": check_sources(cfg, probe=probe),
        "database": check_database(db_path or cfg["_db_path"], schema),
        "config": check_config(cfg),
    }


def render(result: dict) -> str:
    """给人看的版本：必需项不通过要说清怎么办，可选项缺失只提一句。"""
    lines: list[str] = ["===== 环境体检 =====", ""]
    py = result["python"]
    lines.append(f"[{'✓' if py['ok'] else '✗'}] Python {py['version']}　{py['executable']}")
    if not py["ok"]:
        lines.append(f"      → {py['note']}")

    modules = result["modules"]
    for item in modules["required"]:
        mark = "✓" if item["version"] else "✗"
        lines.append(f"[{mark}] 必需依赖 {item['name']}　{item['version'] or '未安装'}"
                     + ("" if item["version"] else "　→ pip install pyyaml（唯一的硬依赖）"))
    installed = [item for item in modules["optional"] if item["version"]]
    missing = [item for item in modules["optional"] if not item["version"]]
    lines.append(f"[{'✓' if not missing else '!'}] 数据源适配器："
                 + ("、".join(f"{i['name']} {i['version']}" for i in installed) or "一个都没装"))
    for item in missing:
        lines.append(f"      · 缺 {item['name']}（{item['why']}）——不影响主流程，"
                     f"想要就双击「0-安装环境」或跑 sources")

    sources = result.get("sources") or {}
    if sources:
        conf = sources["configured"]
        lines.append(
            f"[{'✓' if sources['usable'] else '✗'}] 数据源可用性："
            + (f"{len(sources['usable'])} 个可用（{'、'.join(sources['usable'])}）"
               if sources["usable"] else "一个都用不了"))
        lines.append(f"      配置：主源 {conf.get('primary')}　备份 {conf.get('backup')}"
                     f"　兜底 {conf.get('fallback')}　补充 {conf.get('extra')}")
        for item in sources["items"]:
            if sources["probed"]:
                mark = "✓" if item["ok"] else ("✗" if item["installed"] else "·")
                detail = item["note"] or ("接口通" if item["ok"] else "")
            else:
                mark = "✓" if item["installed"] else "·"
                detail = "已安装" if item["installed"] else (item["note"] or "没装")
            lines.append(f"      [{mark}] {item['name']:<9}{detail}")
        if not sources["probed"]:
            lines.append("      （这只回答「包装没装」；要问「接口通不通」，跑 doctor --sources）")
        elif conf.get("primary") not in sources["usable"]:
            lines.append(f"      → 主源 {conf.get('primary')} 现在不能用，"
                         f"日终会自动改用能用的那个（备选顺序见 config.yaml）")
        gaps = sources.get("gaps") or {}
        if gaps:
            detail = "；".join(f"{cap}（{use}）" for cap, use in gaps.items())
            lines.append(f"      [!] 没有可用源的能力：{detail}")
            lines.append("          这些表的行数会一直是 0，对应的因子/字段只能空着——"
                         "换条网络、或者把代理放行对应域名，再跑一次体检看看。")

    database = result["database"]
    mark = "✓" if database["ok"] else ("!" if database["exists"] else "✗")
    lines.append(f"[{mark}] 数据库 {database['path']}"
                 + (f"　{database['size_mb']} MB　{database['tables']} 张表" if database["exists"] else ""))
    if database["exists"]:
        lines.append(f"      完整性 {database.get('integrity')}　日志模式 {database.get('journal_mode')}"
                     f"　最新日线 {database.get('latest_bar') or '—'}")
        if database["rows"]:
            lines.append("      行数：" + "　".join(f"{k} {v:,}" for k, v in database["rows"].items()))
    if database["note"]:
        lines.append(f"      → {database['note']}")

    config = result["config"]
    lines.append(f"[{'✓' if config['ok'] else '✗'}] 配置：观察池 {config['watchlist']} 只"
                 + (f"（{'、'.join(config['watchlist_codes'][:6])}…）" if config["watchlist"] > 6
                    else f"（{'、'.join(config['watchlist_codes'])}）" if config["watchlist"] else ""))
    if config["note"]:
        lines.append(f"      → {config['note']}")
    push, ai = config["push"], config["ai"]
    lines.append(f"      推送：{'已开启' if push['enabled'] else '未开启'}"
                 f"　token {'已配置' if push['configured'] else '没配'}"
                 + (f"（来源 {push['from']}）" if push["configured"] else ""))
    lines.append(f"      AI 分析：{'已开启' if ai['enabled'] else '未开启'}"
                 f"　key {'已配置' if ai['configured'] else '没配'}"
                 + (f"（来源 {ai['from']}）" if ai["configured"] else ""))
    lines.append("")
    lines.append("说明：体检只读，不改任何东西。数据库不存在时跑 init-db 建空库，")
    lines.append("      数据要跑 3-每日任务 或 18-全市场同步 才会有。")
    return "\n".join(lines)
