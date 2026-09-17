"""命令行入口：init-db / sources / daily / intraday / report / factors / selftest。"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import (
    backtest as backtest_mod,
    dashboard as dashboard_mod,
    db,
    dictionary as dictionary_mod,
    promotion as promotion_mod,
    report as report_mod,
    review as review_mod,
    screen as screen_mod,
    server as server_mod,
    share as share_mod,
    tasks,
    warehouse as warehouse_mod,
)
from .config import load_config, use_fixture_sources
from .sources import SOURCE_REGISTRY, build_source


def _prepare(args) -> dict:
    cfg = load_config(args.config)
    if getattr(args, "db", None):
        cfg["_db_path"] = str(Path(args.db).resolve())
    return cfg


def _connect(cfg: dict):
    schema = Path(cfg["_project_root"]) / "schema.sql"
    conn = db.connect(cfg["_db_path"])
    db.init_db(conn, schema)
    return conn


def cmd_init_db(args) -> int:
    cfg = _prepare(args)
    db_path = Path(cfg["_db_path"])
    if getattr(args, "rebuild", False) and db_path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = db_path.with_name(f"{db_path.name}.bak-{stamp}")
        try:
            db_path.rename(backup)
            for suffix in ("-wal", "-shm"):
                side = Path(str(db_path) + suffix)
                if side.exists():
                    side.rename(Path(str(backup) + suffix))
        except PermissionError:
            print("重建失败：数据库文件正被其他程序占用。")
            print("请先关闭 Navicat / DB Browser 里对 data\\market.db 的连接，再重试。")
            print("（Navicat：右键该连接 → 关闭连接；或直接退出 Navicat）")
            return 1
        print(f"旧库已备份为：{backup.name}（重建后需要重跑一次 daily 才会有数据）")
    conn = _connect(cfg)
    print(f"数据库已就绪：{cfg['_db_path']}")
    conn.close()
    return 0


def cmd_sources(args) -> int:
    """数据源自检：既看装没装，也**真的取一次数**。

    只测"包装没装"没有意义——真正的坑是包在、但接口连不上。
    """
    cfg = _prepare(args)
    today = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d")
    print(f"数据源自检（用 {start} ~ {today} 的沪深300做探针）：")

    for name in SOURCE_REGISTRY:
        if name in ("fixture", "fixture_alt", "csv"):
            continue
        try:
            source = build_source(name, cfg)
            ok, note = source.is_available()
        except Exception as exc:  # 构建期失败也要给出可读结论
            print(f"  {name:<10} 装没装：否    {exc}")
            continue
        if not ok:
            print(f"  {name:<10} 装没装：否    {note}")
            continue
        if "daily_bars" not in getattr(source, "capabilities", set()):
            print(f"  {name:<10} 装没装：是    不支持日线接口")
            continue
        try:
            rows = source.daily_bars("SH000300", start, today, "index")
            print(f"  {name:<10} 装没装：是    ✅ 真取到 {len(rows)} 根日线")
        except Exception as exc:
            print(f"  {name:<10} 装没装：是    ❌ 取数失败：{type(exc).__name__} {str(exc)[:110]}")
    print("")
    print("说明：列出的都是免费源，不需要账号。哪个显示 ✅ 就说明你的网络能连上它；")
    print("      主源连不上时，全市场同步会自动改用能连上的那个（见 config.yaml 的 sources）。")
    return 0


def cmd_daily(args) -> int:
    cfg = _prepare(args)
    conn = _connect(cfg)
    summary = tasks.run_daily(conn, cfg, trade_date=args.date, verbose=not args.quiet)
    print("")
    print(report_mod.daily_report(conn, cfg, summary["trade_date"]))
    conn.close()
    return 0


def cmd_intraday(args) -> int:
    cfg = _prepare(args)
    conn = _connect(cfg)
    results = tasks.run_intraday(conn, cfg, verbose=not args.quiet)
    if not results:
        print("盘中无触发信号。")
    for item in results:
        print(f"[{item['level']}] {item['code']} {item['signal_type']}：{item['message']}")
    conn.close()
    return 0


def cmd_report(args) -> int:
    cfg = _prepare(args)
    conn = _connect(cfg)
    print(report_mod.daily_report(conn, cfg, args.date))
    conn.close()
    return 0


def cmd_factors(args) -> int:
    cfg = _prepare(args)
    conn = _connect(cfg)
    row = db.query_one(conn, "SELECT MAX(trade_date) AS d FROM factor_contributions")
    trade_date = args.date or (row["d"] if row and row["d"] else None)
    if not trade_date:
        print("还没有因子贡献记录。")
        return 1
    print(report_mod.factor_report(conn, cfg, trade_date, args.code))
    conn.close()
    return 0


def cmd_selftest(args) -> int:
    """离线自检：夹具源跑通完整链路，验证状态机、仲裁、提醒与审计是否闭环。"""
    cfg = load_config(args.config)
    use_fixture_sources(cfg)          # 自检必须离线：不看配置里用的是真实源还是夹具源
    if args.db:
        cfg["_db_path"] = str(Path(args.db).resolve())
    else:
        cfg["_db_path"] = str(Path(cfg["_project_root"]) / "data" / "selftest.db")

    Path(cfg["_db_path"]).parent.mkdir(parents=True, exist_ok=True)

    conn = _connect(cfg)
    summary = tasks.run_daily(conn, cfg, verbose=not args.quiet)
    print("")
    print(report_mod.daily_report(conn, cfg, summary["trade_date"]))

    checks: list[tuple[str, bool, str]] = []
    alerts = db.query(conn, "SELECT COUNT(*) AS n FROM alerts")
    states = db.query(conn, "SELECT state, COUNT(*) AS n FROM features_daily GROUP BY state")
    contributions = db.query(conn, "SELECT COUNT(*) AS n FROM factor_contributions")
    arbitrations = db.query(conn, "SELECT COUNT(*) AS n FROM arbitration_log")
    levels = db.query(conn, "SELECT COUNT(*) AS n FROM levels")

    checks.append(("生成了提醒", alerts[0]["n"] > 0, f"{alerts[0]['n']} 条"))
    checks.append(("状态机产出多种状态", len(states) >= 2, str({row["state"]: row["n"] for row in states})))
    checks.append(("因子贡献已留痕", contributions[0]["n"] > 0, f"{contributions[0]['n']} 行"))
    checks.append(("关键带已生成", levels[0]["n"] > 0, f"{levels[0]['n']} 条"))
    checks.append(("仲裁日志可用", True, f"{arbitrations[0]['n']} 行（无冲突时为 0）"))

    print("")
    print("===== 自检结果 =====")
    failed = 0
    for name, ok, detail in checks:
        print(f"  [{'通过' if ok else '失败'}] {name}：{detail}")
        failed += 0 if ok else 1
    conn.close()
    return 1 if failed else 0


def cmd_dashboard(args) -> int:
    cfg = _prepare(args)
    conn = _connect(cfg)
    target = dashboard_mod.write(conn, cfg, args.date, args.out)
    print(f"简报页面已生成：{target}")
    conn.close()
    return 0


def cmd_dictionary(args) -> int:
    cfg = _prepare(args)
    conn = _connect(cfg)
    info = dictionary_mod.build(conn)
    print(f"已写入 data_dictionary：{info['tables']} 张表 / {info['columns']} 个字段")
    if info["missing"]:
        print("以下字段还没有中文说明，请补到 collector/dictionary.py：")
        for item in info["missing"]:
            print(f"  - {item}")
    target = dictionary_mod.write_markdown(Path(cfg["_project_root"]) / "字段说明.md")
    print(f"字段说明文档：{target}")
    conn.close()
    return 0


def cmd_web(args) -> int:
    """起本地看盘页面（K 线 + 指标 + 自选管理）。"""
    cfg = _prepare(args)
    return server_mod.serve(cfg, port=args.port, open_browser=not args.no_open)


def cmd_share(args) -> int:
    """生成可以发微信的看板图（PNG）。"""
    cfg = _prepare(args)
    codes = [args.code] if args.code else None
    out_dir = Path(args.out).resolve() if args.out else None
    result = share_mod.generate(cfg, codes=codes, days=args.days, out_dir=out_dir)

    print(f"\n输出目录：{result['out_dir']}")
    if result["skipped"]:
        print("没有数据、跳过的标的：" + "、".join(result["skipped"]))
    if not result["ok"]:
        print("没有可生成的标的。先双击 3-每日任务 把数据抓下来。")
        return 1
    if result["renderer"]:
        print(f"渲染方式：{result['renderer']}，共 {len(result['images'])} 张")
        print("图片可以直接拖进微信发送。")
    else:
        print("没找到可用的浏览器（Chrome / Edge / Playwright），已经把卡片留成 HTML：")
        print("  - 用浏览器打开这些 HTML，页面就是 1080×1720 的整图，直接截图即可")
        print("  - 或双击 0-安装环境 之后重试（会自动带上 Playwright）")
    return 0


def cmd_review(args) -> int:
    """回填提醒表现并打印胜率复盘。"""
    cfg = _prepare(args)
    conn = _connect(cfg)
    if not args.no_backfill:
        info = review_mod.backfill_outcomes(conn, verbose=False)
        print(f"回填：提醒 {info['alerts']} 条、被压制信号 {info['conflicts']} 条")
    print("")
    print(review_mod.report(conn))
    conn.close()
    return 0


def cmd_backtest(args) -> int:
    """扫状态机参数网格，看哪组真的有效。"""
    cfg = _prepare(args)
    conn = _connect(cfg)
    grid = None
    if args.enter_up:
        grid = dict(backtest_mod.DEFAULT_GRID)
        grid["enter_up"] = tuple(int(v) for v in args.enter_up.split(","))
    if args.confirm:
        grid = grid or dict(backtest_mod.DEFAULT_GRID)
        grid["confirm_days"] = tuple(int(v) for v in args.confirm.split(","))
    if args.min_days:
        grid = grid or dict(backtest_mod.DEFAULT_GRID)
        grid["min_state_days"] = tuple(int(v) for v in args.min_days.split(","))

    result = backtest_mod.run_grid(conn, cfg, grid=grid)
    conn.close()
    if not result.get("ok"):
        print(result.get("message", "回测失败"))
        return 1
    text = backtest_mod.render_report(result)
    print("")
    print(text)
    if not args.no_save:
        target = backtest_mod.write_report(text, cfg["_project_root"])
        print("")
        print(f"报告已保存：{target}")
    return 0


def _print_warehouse_status(conn) -> None:
    info = warehouse_mod.status(conn)
    print(f"  本地仓库：代码表 {info['instruments']} 只，"
          f"有日线 {info['with_data']} 只，共 {info['bars']} 根，最新 {info['latest'] or '—'}")
    if info["snapshot_only"]:
        print(f"  其中 {info['snapshot_only']} 只只有快照数据（不复权，仅够看和筛）")


def cmd_sync(args) -> int:
    """全市场本地化：先拉代码表，再分批补历史（可以反复跑，自动续）。"""
    cfg = _prepare(args)
    conn = _connect(cfg)
    _print_warehouse_status(conn)
    print("")

    if not args.skip_universe:
        info = warehouse_mod.sync_universe(conn, cfg)
        if not info.get("ok"):
            print(f"代码表没拉到：{info.get('message')}")
            if not warehouse_mod.universe_codes(conn):
                conn.close()
                return 1
            print("（用本地已有的代码表继续）")

    result = warehouse_mod.sync_history(conn, cfg, limit=args.limit, days=args.days)
    if not result.get("ok"):
        print(result.get("message", "同步失败"))
        conn.close()
        return 1
    print("")
    _print_warehouse_status(conn)
    conn.close()
    return 0


def cmd_snapshot(args) -> int:
    """手动跑一次全市场快照落库（平时日终任务会自动做）。"""
    cfg = _prepare(args)
    conn = _connect(cfg)
    result = warehouse_mod.snapshot_bars(conn, cfg, args.date)
    if result.get("ok"):
        print(f"已写入 {result['written']} 只标的的 {result['trade_date']} 日 K 线"
              f"（跳过 {result['skipped']} 只有正式数据的）")
    else:
        print(f"没写成：{result.get('message')}")
    conn.close()
    return 0 if result.get("ok") else 1


def cmd_shadow(args) -> int:
    """因子台账体检：影子因子够不够格转正、现有因子有没有冗余。"""
    cfg = _prepare(args)
    conn = _connect(cfg)
    print(promotion_mod.report(promotion_mod.factor_stats(conn, cfg)))
    conn.close()
    return 0


def cmd_pack(args) -> int:
    """把数据库打包成一个可以拷到另一台电脑的文件。"""
    cfg = _prepare(args)
    result = warehouse_mod.pack_database(cfg, out_dir=args.out, compress=not args.plain)
    if not result.get("ok"):
        print(result.get("message", "打包失败"))
        return 1
    print("")
    print("拷到另一台电脑后：把 market.db 放回项目的 data/ 目录（覆盖同名文件），")
    print("然后双击 14-看盘页面 就能看；想继续抓数据就双击 3-每日任务。")
    return 0


def cmd_add(args) -> int:
    """批量加自选：一只一只抓历史太慢时用这个，支持空格或逗号分隔。"""
    cfg = _prepare(args)
    raw = []
    for item in args.codes:
        raw.extend(part for part in item.replace("，", ",").split(",") if part.strip())
    if not raw:
        print("没给代码。用法：python run.py add 600519 000001 SH510300")
        return 1

    app = server_mod.App(cfg)
    added, failed = [], []
    for text in raw:
        result = app.add(text)
        if result.get("ok"):
            added.append(f"{result['code']}（{result.get('days', 0)} 根日线）")
            print(f"  + {result['code']}  {result.get('days', 0)} 根日线")
        else:
            failed.append(f"{text}：{result.get('message')}")
            print(f"  ! {text}  {result.get('message')}")
    print("")
    print(f"完成：加入 {len(added)} 只，失败 {len(failed)} 只")
    if failed:
        print("失败明细：" + "；".join(failed[:5]))
    return 0 if added else 1


def cmd_screen(args) -> int:
    """全市场筛选：把本地有历史的票按形态扫一遍。"""
    cfg = _prepare(args)
    conn = _connect(cfg)
    result = screen_mod.scan(conn, cfg, min_bars=args.min_bars, limit=args.limit)
    if result.get("ok"):
        screen_mod.sync_criteria(conn, cfg, result.get("trade_date"))
    if not result.get("ok"):
        print(result.get("message", "筛选失败"))
        conn.close()
        return 1
    text = screen_mod.report(result, top=args.top)
    if not args.no_save:
        saved = screen_mod.save(conn, result, top=args.top)
        print(f"  结果已入库 {saved} 条（看盘页面可以直接看）")
        target = Path(cfg["_project_root"]) / "筛选" / f"筛选结果-{result['trade_date']}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    print("")
    print(text)
    if not args.no_save:
        print("")
        print(f"报告已保存：{target}")
    conn.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="collector", description="A 股个人交易提醒系统 · 采集骨架")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, handler, help_text in (
        ("init-db", cmd_init_db, "建库/升级表结构"),
        ("sources", cmd_sources, "探测数据源可用性"),
        ("intraday", cmd_intraday, "跑一次盘中检查"),
        ("selftest", cmd_selftest, "用离线夹具跑通全链路并自检"),
    ):
        item = sub.add_parser(name, help=help_text)
        item.add_argument("--db", help="数据库路径（默认取配置）")
        item.add_argument("--quiet", action="store_true", help="精简输出")
        item.set_defaults(func=handler)

    init = sub.choices["init-db"]
    init.add_argument("--rebuild", action="store_true", help="备份旧库并重建（让新的建表注释生效）")

    daily = sub.add_parser("daily", help="跑一次日终任务")
    daily.add_argument("--date", help="交易日 YYYY-MM-DD")
    daily.add_argument("--db", help="数据库路径")
    daily.add_argument("--quiet", action="store_true")
    daily.set_defaults(func=cmd_daily)

    report = sub.add_parser("report", help="输出简报")
    report.add_argument("--date", help="交易日 YYYY-MM-DD")
    report.add_argument("--db", help="数据库路径")
    report.set_defaults(func=cmd_report)

    factors = sub.add_parser("factors", help="回放某只标的的因子贡献")
    factors.add_argument("code", help="标的代码，如 SH000300")
    factors.add_argument("--date", help="交易日 YYYY-MM-DD")
    factors.add_argument("--db", help="数据库路径")
    factors.set_defaults(func=cmd_factors)

    dash = sub.add_parser("dashboard", help="生成简报页面（单文件 HTML）")
    dash.add_argument("--date", help="交易日 YYYY-MM-DD")
    dash.add_argument("--out", help="输出路径，默认项目根目录 dashboard.html")
    dash.add_argument("--db", help="数据库路径")
    dash.set_defaults(func=cmd_dashboard)

    dictionary = sub.add_parser("dictionary", help="生成数据字典与字段说明文档")
    dictionary.add_argument("--db", help="数据库路径")
    dictionary.set_defaults(func=cmd_dictionary)

    web = sub.add_parser("web", help="打开本地看盘页面（K 线 / 指标 / 自选）")
    web.add_argument("--port", type=int, default=8765, help="端口，默认 8765")
    web.add_argument("--no-open", action="store_true", help="不要自动打开浏览器")
    web.add_argument("--db", help="数据库路径")
    web.set_defaults(func=cmd_web)

    share = sub.add_parser("share", help="生成可以发微信的看板图（PNG）")
    share.add_argument("code", nargs="?", help="标的代码；不填就生成整份自选")
    share.add_argument("--days", type=int, default=120, help="图上画多少根 K 线，默认 120")
    share.add_argument("--out", help="输出目录，默认项目根目录下的 分享图/")
    share.add_argument("--db", help="数据库路径")
    share.set_defaults(func=cmd_share)

    review = sub.add_parser("review", help="回填提醒表现并统计胜率")
    review.add_argument("--no-backfill", action="store_true", help="只出报告，不写回数据库")
    review.add_argument("--db", help="数据库路径")
    review.set_defaults(func=cmd_review)

    backtest = sub.add_parser("backtest", help="扫状态机参数，看哪组真的有效")
    backtest.add_argument("--enter-up", help="进入上升的阈值，逗号分隔，例如 60,65,70,75")
    backtest.add_argument("--confirm", help="确认天数，例如 1,2,3")
    backtest.add_argument("--min-days", help="最短持续期，例如 1,3,5")
    backtest.add_argument("--no-save", action="store_true", help="不写报告文件")
    backtest.add_argument("--db", help="数据库路径")
    backtest.set_defaults(func=cmd_backtest)

    sync = sub.add_parser("sync", help="全市场本地化：代码表 + 分批补历史（可反复跑）")
    sync.add_argument("--limit", type=int, help="本次最多补多少只，默认取配置 warehouse.batch_size")
    sync.add_argument("--days", type=int, help="首次回补拉多少天，默认取配置 warehouse.history_days")
    sync.add_argument("--skip-universe", action="store_true", help="不更新代码表，只补历史")
    sync.add_argument("--db", help="数据库路径")
    sync.set_defaults(func=cmd_sync)

    snapshot = sub.add_parser("snapshot", help="用一次全市场快照补当日 K 线")
    snapshot.add_argument("--date", help="交易日，默认取本地最新交易日")
    snapshot.add_argument("--db", help="数据库路径")
    snapshot.set_defaults(func=cmd_snapshot)

    shadow = sub.add_parser("shadow", help="因子台账体检：影子因子覆盖率 / 相关性 / IC")
    shadow.add_argument("--db", help="数据库路径")
    shadow.set_defaults(func=cmd_shadow)

    pack = sub.add_parser("pack", help="打包数据库，拷到另一台电脑用")
    pack.add_argument("--out", help="输出目录，默认项目根目录下的 备份/")
    pack.add_argument("--plain", action="store_true", help="不压缩，直接复制成 .db 文件")
    pack.add_argument("--db", help="数据库路径")
    pack.set_defaults(func=cmd_pack)

    add = sub.add_parser("add", help="批量加入自选（空格或逗号分隔）")
    add.add_argument("codes", nargs="+", help="代码，例如 600519 000001 SH510300")
    add.add_argument("--db", help="数据库路径")
    add.set_defaults(func=cmd_add)

    screen = sub.add_parser("screen", help="全市场筛选：按形态扫本地全部标的")
    screen.add_argument("--min-bars", type=int, help="至少多少根日线才参与（默认取配置 screen.min_bars）")
    screen.add_argument("--top", type=int, help="每个条件保留多少条（默认取配置 screen.top）")
    screen.add_argument("--limit", type=int, help="只扫前 N 只（调试用）")
    screen.add_argument("--no-save", action="store_true", help="只打印，不落库不写文件")
    screen.add_argument("--db", help="数据库路径")
    screen.set_defaults(func=cmd_screen)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
