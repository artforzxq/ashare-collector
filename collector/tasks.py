"""任务编排：日终采集 → 校验 → 特征与状态 → 关键带 → 仲裁 → 提醒落库。"""

from __future__ import annotations

import statistics
import time
from datetime import datetime, timedelta
from typing import Iterable

from . import candles as candles_mod, db, features as features_mod, review as review_mod, risk as risk_mod, validate, warehouse
from .names import display_name
from .alerts import apply_budget, apply_cooldown, build_candidates, persist
from .arbitrate import DecisionContext, arbitrate
from .config import watchlist_codes
from .levels import build_levels, nearest_band
from .registry import FactorRegistry
from .sources import DataSourceError, build_source


def _known_name(conn, code: str) -> str:
    """标的名称：优先用代码表里的真名，其次用内置的中文名，最后才退回代码。

    之前这里直接写 code，把全市场代码表拉回来的名称又覆盖掉了。
    """
    row = db.query_one(conn, "SELECT name FROM instruments WHERE code=?", (code,))
    existing = (row["name"] if row else "") or ""
    if existing and existing != code:
        return existing
    return display_name(code) or code


def _log(message: str, verbose: bool) -> None:
    if verbose:
        print(message, flush=True)


def resolve_trade_date(source, cfg: dict, provided: str | None) -> str:
    if provided:
        return provided
    if hasattr(source, "_dates") and source._dates:  # 夹具源
        return source._dates[-1]
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    try:
        calendar = source.trade_calendar(start, end)
        days = [row["trade_date"] for row in calendar if row.get("is_trading_day")]
        if days:
            return days[-1]
    except (DataSourceError, NotImplementedError):
        pass
    return end


def feature_version(cfg: dict) -> str:
    return str(cfg["project"].get("feature_version", "v1"))


def _source_pool(cfg: dict) -> list:
    """按 主源 → 备份源 → 兜底源 建好可用数据源列表（同名去重）。"""
    pool: list = []
    seen: set[str] = set()
    for name in (cfg["sources"].get("primary"), cfg["sources"].get("backup"), cfg["sources"].get("fallback")):
        if not name or name in seen:
            continue
        seen.add(name)
        try:
            pool.append(build_source(name, cfg))
        except Exception:
            continue
    return pool


def _source_for(pool: list, capability: str):
    """挑第一个声明支持该能力的数据源，都不支持就返回 None。

    依据是适配器自己的 capabilities：baostock 只有日线和交易日历，
    akshare 才有全市场快照、ETF 份额、融资融券和盘中快照。
    没有这一步，换成真实源之后这几张表永远是空的。
    """
    for source in pool:
        if capability in getattr(source, "capabilities", set()):
            return source
    return None


# ---------- 日终任务 ----------

def run_daily(conn, cfg: dict, trade_date: str | None = None, history_days: int = 460, verbose: bool = True) -> dict:
    registry = FactorRegistry(cfg.get("factors", []), feature_version(cfg))
    primary = build_source(cfg["sources"]["primary"], cfg)
    backup = build_source(cfg["sources"]["backup"], cfg)
    pool = _source_pool(cfg)
    trade_date = resolve_trade_date(primary, cfg, trade_date)
    start = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=history_days)).strftime("%Y-%m-%d")

    summary = {"trade_date": trade_date, "codes": {}, "breadth": None, "alerts": [], "issues": []}
    _log(f"[1/7] 交易日 {trade_date}，主源 {primary.name}，备份源 {backup.name}", verbose)

    violations = registry.category_budget_violations()
    for problem in violations:
        summary["issues"].append(f"类别权重预算：{problem}")
        _log(f"      ! {problem}", verbose)
    registry.sync_to_db(conn, trade_date)

    items = watchlist_codes(cfg)
    names = {item["code"]: _known_name(conn, item["code"]) for item in items}
    db.upsert_rows(
        conn,
        "instruments",
        [
            {
                "code": item["code"],
                "name": names[item["code"]],
                "type": item["type"],
                "exchange": item["code"][:2],
                "role": item["role"],
                "in_watchlist": 1,
                "updated_at": db.now_iso(),
            }
            for item in items
        ],
        ["code"],
    )

    _log("[2/7] 拉取日线并做双源交叉校验", verbose)
    missing: list[str] = []
    all_conflicts: list[dict] = []
    for item in items:
        code, kind = item["code"], item["type"]
        started = time.time()
        primary_rows = _fetch(primary, code, start, trade_date, kind, summary)
        backup_rows = _fetch(backup, code, start, trade_date, kind, summary)

        if not primary_rows and not backup_rows:
            db.log_health(conn, trade_date, primary.name, f"daily:{code}", "failed", 0, 1.0, 0, "无数据")
            continue

        merge = validate.merge_two_sources(primary_rows or backup_rows, backup_rows, cfg)
        checked = validate.validate_bars(merge.rows, cfg)
        for row in checked.rows:
            row["source"] = row.get("source") or primary.name
            row["updated_at"] = db.now_iso()
        db.upsert_rows(conn, "bars_daily", checked.rows, ["code", "trade_date"])

        all_conflicts.extend(merge.conflicts)
        summary["codes"][code] = {
            "rows": len(checked.rows),
            "quality": merge.quality_flag if checked.quality_flag == "ok" else checked.quality_flag,
            "conflicts": len(merge.conflicts),
            "blocked": len(checked.blocked),
        }
        db.log_health(
            conn,
            trade_date,
            primary.name,
            f"daily:{code}",
            "ok" if not checked.blocked else "retry",
            len(checked.rows),
            0.0,
            int((time.time() - started) * 1000),
            "; ".join(checked.notes) or None,
        )
        for note in merge.notes:
            _log(f"      - {code}: {note}", verbose)

    # 「缺失」以库里到底有没有这一天的数据为准，而不是这次请求成不成功：
    # 重跑时抓数失败，不该把已经入库的完整数据判成缺失并冻结当日提醒。
    missing = [
        item["code"]
        for item in items
        if db.query_one(
            conn, "SELECT 1 FROM bars_daily WHERE code=? AND trade_date=?", (item["code"], trade_date)
        )
        is None
    ]
    level, message = validate.grade_missing(missing, cfg["validation"].get("critical_codes", []))
    if level != "ok":
        summary["issues"].append(f"缺失分级 {level}：{message}")
        _log(f"      ! 缺失分级 {level}：{message}", verbose)
    summary["missing_grade"] = level

    _log("[3/7] 计算市场广度", verbose)
    summary["breadth"] = _collect_breadth(conn, _source_for(pool, "market_snapshot"), cfg, trade_date, verbose)
    _collect_market_snapshot_bars(conn, cfg, trade_date, verbose)

    _log("[4/7] 采集交易日历、ETF 份额与杠杆资金", verbose)
    _collect_calendar(conn, _source_for(pool, "trade_calendar"), cfg, trade_date, verbose)
    _collect_etf_shares(conn, _source_for(pool, "etf_shares"), cfg, trade_date, verbose)
    _collect_margin(conn, _source_for(pool, "margin"), trade_date, verbose)

    _log("[5/7] 计算特征与状态（迟滞 + 确认 + 最短持续期）", verbose)
    state_rows = _compute_features(conn, cfg, registry, trade_date, verbose)

    _log("[6/7] 计算关键带", verbose)
    bands_by_code = _compute_levels(conn, cfg, trade_date, verbose)
    _apply_risk(conn, cfg, trade_date, state_rows, bands_by_code, verbose)

    _log("[7/7] 仲裁并生成提醒", verbose)
    alerts = _decide(conn, cfg, trade_date, state_rows, bands_by_code, level, summary, verbose)
    summary["alerts"] = alerts

    # 顺手回填历史提醒的实际表现（复盘要用），失败不影响当日流程
    try:
        review_mod.backfill_outcomes(conn, verbose=verbose)
    except Exception as exc:
        _log(f"      ! 回填提醒表现失败：{exc}", verbose)
    return summary


def _fetch(source, code: str, start: str, end: str, kind: str, summary: dict) -> list[dict]:
    try:
        return source.daily_bars(code, start, end, kind)
    except Exception as exc:  # 适配器可能抛出 requests/urllib3 等原生异常，不能让整条日终任务崩掉
        summary["issues"].append(f"{source.name} 拉取失败 {code}：{exc}")
        return []


def _collect_breadth(conn, source, cfg: dict, trade_date: str, verbose: bool) -> dict | None:
    if source is None:
        _log("      ! 没有支持全市场快照的数据源，市场广度跳过", verbose)
        db.log_health(conn, trade_date, "none", "breadth", "failed", 0, 1.0, 0, "无支持 market_snapshot 的数据源")
        return None
    try:
        snapshot = source.market_snapshot(trade_date)
    except Exception as exc:
        _log(f"      ! 广度数据不可用：{exc}", verbose)
        db.log_health(conn, trade_date, source.name, "breadth", "failed", 0, 1.0, 0, str(exc))
        return None

    pcts = [float(row["pct_chg"]) for row in snapshot if row.get("pct_chg") is not None]
    if not pcts:
        return None
    up = sum(1 for p in pcts if p > 0)
    down = sum(1 for p in pcts if p < 0)
    flat = len(pcts) - up - down
    record = {
        "trade_date": trade_date,
        "up_count": up,
        "down_count": down,
        "flat_count": flat,
        "limit_up_count": sum(1 for p in pcts if p >= 9.8),
        "limit_down_count": sum(1 for p in pcts if p <= -9.8),
        "broken_limit_count": None,   # 需要连板数据，第二阶段补
        "max_boards": None,           # 同上
        "up_ratio": round(up / max(1, up + down), 4),
        "median_pct_chg": round(statistics.median(pcts), 4),
        "total_amount": sum(float(row.get("amount") or 0) for row in snapshot),
        "sh_amount": sum(float(row.get("amount") or 0) for row in snapshot if str(row.get("code", "")).startswith("SH")),
        "sz_amount": sum(float(row.get("amount") or 0) for row in snapshot if str(row.get("code", "")).startswith("SZ")),
        "source": source.name,
        "updated_at": db.now_iso(),
    }
    db.upsert_rows(conn, "market_breadth", [record], ["trade_date"])
    _log(f"      上涨 {up} / 下跌 {down}，涨停 {record['limit_up_count']}，中位数 {record['median_pct_chg']}%", verbose)
    return record


def _collect_market_snapshot_bars(conn, cfg: dict, trade_date: str, verbose: bool) -> None:
    """把全市场快照写成本地 K 线（只写观察池以外的票）。

    这是"每天只拉最新一根"的实现：一次请求覆盖全市场，不用逐只去问。
    观察池里的票由日终任务用复权数据写，这里会跳过，避免两种口径混进同一条序列。
    """
    if not (cfg.get("warehouse") or {}).get("snapshot_daily", True):
        return
    try:
        result = warehouse.snapshot_bars(conn, cfg, trade_date, verbose=False)
    except Exception as exc:
        _log(f"      ! 全市场快照写库失败：{exc}", verbose)
        return
    if result.get("ok"):
        _log(f"      全市场快照：{result['written']} 只写入当日 K 线", verbose)
    else:
        _log(f"      ! 全市场快照未写入：{result.get('message')}", verbose)


def _collect_calendar(conn, source, cfg: dict, trade_date: str, verbose: bool) -> None:
    """交易日历落库：补最近 400 天，顺手算每个交易日的上一日/下一日。"""
    if source is None:
        _log("      ! 没有支持交易日历的数据源", verbose)
        db.log_health(conn, trade_date, "none", "trade_calendar", "failed", 0, 1.0, 0, "无支持 trade_calendar 的数据源")
        return
    start = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=400)).strftime("%Y-%m-%d")
    try:
        rows = source.trade_calendar(start, trade_date)
    except Exception as exc:
        _log(f"      ! 交易日历不可用：{exc}", verbose)
        db.log_health(conn, trade_date, source.name, "trade_calendar", "failed", 0, 1.0, 0, str(exc))
        return

    trading = sorted({r["trade_date"] for r in rows if int(r.get("is_trading_day") or 0) == 1})
    if not trading:
        _log("      ! 交易日历为空", verbose)
        return

    index_of = {day: i for i, day in enumerate(trading)}
    payload: list[dict] = []
    cursor = datetime.strptime(trading[0], "%Y-%m-%d")
    last_day = datetime.strptime(trading[-1], "%Y-%m-%d")
    while cursor <= last_day:
        text = cursor.strftime("%Y-%m-%d")
        pos = index_of.get(text)
        payload.append(
            {
                "trade_date": text,
                "is_trading_day": 1 if pos is not None else 0,
                "prev_trade_date": trading[pos - 1] if pos else None,
                "next_trade_date": trading[pos + 1] if pos is not None and pos + 1 < len(trading) else None,
            }
        )
        cursor += timedelta(days=1)
    db.upsert_rows(conn, "trade_calendar", payload, ["trade_date"])
    _log(f"      交易日历 {trading[0]} ~ {trading[-1]}，其中 {len(trading)} 个交易日", verbose)


def _collect_etf_shares(conn, source, cfg: dict, trade_date: str, verbose: bool) -> None:
    if source is None:
        _log("      ! 没有支持 ETF 份额的数据源", verbose)
        db.log_health(conn, trade_date, "none", "etf_shares", "failed", 0, 1.0, 0, "无支持 etf_shares 的数据源")
        return
    etf_codes = cfg["watchlist"].get("etfs", [])
    if not etf_codes:
        return
    try:
        rows = source.etf_shares(etf_codes, trade_date)
    except Exception as exc:
        _log(f"      ! ETF 份额不可用：{exc}", verbose)
        db.log_health(conn, trade_date, source.name, "etf_shares", "failed", 0, 1.0, 0, str(exc))
        return
    # 收盘价直接用当天已入库的日线，省掉一次全市场 ETF 行情请求；
    # 折溢价和规模在这里一起算好，适配器只负责取份额和净值。
    closes = {
        row["code"]: row["close"]
        for row in db.query(conn, "SELECT code, close FROM bars_daily WHERE trade_date=?", (trade_date,))
    }
    for row in rows:
        if not row.get("close"):
            row["close"] = closes.get(row["code"])
        nav, close = row.get("nav"), row.get("close")
        if nav and close:
            row["premium_rate"] = round((close / nav - 1) * 100, 4)
            if row.get("shares"):
                row["assets"] = round(row["shares"] * nav, 2)
        row["updated_at"] = db.now_iso()
    db.upsert_rows(conn, "etf_shares", rows, ["code", "trade_date"])


def _collect_margin(conn, source, trade_date: str, verbose: bool) -> None:
    if source is None:
        _log("      ! 没有支持融资融券的数据源", verbose)
        return
    try:
        rows = source.margin(trade_date)
    except Exception as exc:
        _log(f"      ! 融资余额不可用：{exc}", verbose)
        return
    db.upsert_rows(conn, "margin", rows, ["trade_date", "market"])


def _extra_series(conn, cfg: dict, trade_date: str) -> dict:
    """给影子因子提供外部序列：市场广度分与 ETF 份额变化。"""
    breadth_rows = db.query(
        conn, "SELECT trade_date, up_ratio FROM market_breadth WHERE trade_date <= ? ORDER BY trade_date", (trade_date,)
    )
    breadth_score = {
        row["trade_date"]: round((float(row["up_ratio"]) - 0.5) * 2, 4) for row in breadth_rows if row["up_ratio"] is not None
    }

    share_rows = db.query(
        conn, "SELECT code, trade_date, shares FROM etf_shares WHERE trade_date <= ? ORDER BY code, trade_date", (trade_date,)
    )
    by_code: dict[str, list] = {}
    for row in share_rows:
        by_code.setdefault(row["code"], []).append(row)

    etf_share_chg: dict[str, float] = {}
    for rows in by_code.values():
        for index, row in enumerate(rows):
            if index < 5 or not rows[index - 5]["shares"]:
                continue
            etf_share_chg[row["trade_date"]] = round(row["shares"] / rows[index - 5]["shares"] - 1, 4)
    return {"breadth_score": breadth_score, "etf_share_chg": etf_share_chg}


def _compute_features(
    conn, cfg: dict, registry: FactorRegistry, trade_date: str, verbose: bool, codes: list[str] | None = None
) -> dict[str, dict]:
    extra = _extra_series(conn, cfg, trade_date)
    version = feature_version(cfg)
    # 因子贡献保留多少个交易日。原来写死 30 天，"任意一条提醒都能从贡献表还原"这条
    # 验收标准和影子因子满 20 个交易日的准入条件就永远达不成。10 只标的 × 9 因子 × 500 天
    # 也就四万多行，不值得为体积牺牲审计。
    keep_days = int((cfg.get("collection") or {}).get("factor_keep_days", 500))
    latest: dict[str, dict] = {}

    for item in _select_items(cfg, codes):
        code = item["code"]
        bars = db.query(
            conn,
            """SELECT * FROM bars_daily
               WHERE code=? AND trade_date<=? AND COALESCE(quality_flag,'ok')!='blocked'
               ORDER BY trade_date""",
            (code, trade_date),
        )
        bars = [dict(row) for row in bars]
        if len(bars) < 60:
            _log(f"      - {code}: 历史不足（{len(bars)} 根），跳过特征计算", verbose)
            continue

        series = features_mod.compute_feature_series(bars, cfg, registry, extra)
        payload = []
        contributions = []
        for row in series:
            payload.append(
                {
                    "code": code,
                    "trade_date": row["trade_date"],
                    "ma20": row["ma20"],
                    "ma60": row["ma60"],
                    "ma120": row["ma120"],
                    "ma_slope_20": row["ma_slope_20"],
                    "ma_align": row["ma_align"],
                    "adx14": row["adx14"],
                    "atr14": row["atr14"],
                    "atr_pct": row["atr_pct"],
                    "vol_ratio_20": row["vol_ratio_20"],
                    "amount_zscore": row["amount_zscore"],
                    "dist_to_high_250": row["dist_to_high_250"],
                    "donchian_break": row["donchian_break"],
                    "consolidation_days": row["consolidation_days"],
                    "range_width_pct": row["range_width_pct"],
                    "vol_shrink_ratio": row["vol_shrink_ratio"],
                    "breakout_confirmed": row["breakout_confirmed"],
                    "trend_score": row["trend_score"],
                    "range_score": row["range_score"],
                    "opportunity_score": row["opportunity_score"],
                    "state": row["state"],
                    "state_since": row["state_since"],
                    "state_days": row["state_days"],
                    "data_quality_flag": row["quality_flag"],
                    "feature_version": version,
                    "updated_at": db.now_iso(),
                }
            )
            if row["trade_date"] >= _recent_cutoff(series, keep_days):
                for contribution in row["contributions"]:
                    contributions.append(
                        {
                            "trade_date": row["trade_date"],
                            "code": code,
                            "factor_id": contribution["factor_id"],
                            "raw_value": contribution["raw_value"],
                            "normalized_score": contribution["normalized_score"],
                            "weight": contribution["weight"],
                            "contribution": contribution["contribution"],
                            "feature_version": version,
                        }
                    )

        db.upsert_rows(conn, "features_daily", payload, ["code", "trade_date"])
        db.upsert_rows(conn, "factor_contributions", contributions, ["trade_date", "code", "factor_id"])
        latest[code] = series[-1]
        _log(
            f"      - {code}: 状态 {series[-1]['state']}（持续 {series[-1]['state_days']} 日）"
            f"，趋势分 {series[-1]['trend_score']}",
            verbose,
        )
    return latest


def _recent_cutoff(series: list[dict], days: int) -> str:
    return series[-days]["trade_date"] if len(series) >= days else series[0]["trade_date"]


def _select_items(cfg: dict, codes: list[str] | None) -> list[dict]:
    """观察池标的；给了 codes 就只取这几个（页面里新加自选时按需补数据用）。"""
    items = watchlist_codes(cfg)
    if codes is None:
        return items
    wanted = set(codes)
    return [item for item in items if item["code"] in wanted]


def _apply_risk(conn, cfg: dict, trade_date: str, state_rows: dict[str, dict],
                bands_by_code: dict[str, list[dict]], verbose: bool) -> int:
    """风险层：给每个标的算仓位上限和止损位，写回 features_daily。

    风险层不做方向判断（那是状态层的事），只回答"在当前结论下能承受多大风险"。
    K 线形态在这里参与判定：下跌趋势里只有出现反转确认，才允许试探仓。
    """
    updated = 0
    for code, row in state_rows.items():
        bars = [
            dict(item)
            for item in db.query(
                conn,
                """SELECT * FROM bars_daily WHERE code=? AND trade_date<=?
                   AND COALESCE(quality_flag,'ok')!='blocked'
                   ORDER BY trade_date DESC LIMIT 30""",
                (code, trade_date),
            )
        ][::-1]
        candle = candles_mod.analyze(bars, len(bars) - 1) if len(bars) >= 3 else {}
        result = risk_mod.assess(row, bands_by_code.get(code, []), cfg, candle)
        conn.execute(
            """UPDATE features_daily
                  SET position_cap=?, stop_level=?, risk_reward=?, candle_pattern=?, risk_note=?
                WHERE code=? AND trade_date=?""",
            (result["position_cap"], result["stop_level"], result["risk_reward"],
             candles_mod.describe(candle), "；".join(result.get("notes") or []), code, trade_date),
        )
        updated += 1
        if verbose and result.get("position_cap"):
            _log(f"      - {code}: {risk_mod.describe(result)}｜{candles_mod.describe(candle)}", verbose)
    conn.commit()
    return updated


def _compute_levels(conn, cfg: dict, trade_date: str, verbose: bool, codes: list[str] | None = None) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for item in _select_items(cfg, codes):
        code = item["code"]
        bars = db.query(
            conn,
            "SELECT * FROM bars_daily WHERE code=? AND trade_date<=? ORDER BY trade_date",
            (code, trade_date),
        )
        bars = [dict(row) for row in bars]
        if len(bars) < 30:
            continue
        latest_close = bars[-1]["close"]
        bands = build_levels(bars, latest_close, cfg)
        conn.execute("DELETE FROM levels WHERE code=? AND trade_date=?", (code, trade_date))
        db.upsert_rows(
            conn,
            "levels",
            [
                {
                    "code": code,
                    "trade_date": trade_date,
                    "level_type": band["level_type"],
                    "price_low": band["price_low"],
                    "price_high": band["price_high"],
                    "weight": band["weight"],
                    "engine": band["engine"],
                }
                for band in bands
            ],
            ["code", "trade_date", "level_type", "price_low", "price_high"],
        )
        result[code] = bands
        if bands:
            _log(
                f"      - {code}: 关键带 "
                + "，".join(f"{b['level_type']} {b['price_low']}–{b['price_high']}" for b in bands),
                verbose,
            )
    return result


def _decide(
    conn,
    cfg: dict,
    trade_date: str,
    state_rows: dict[str, dict],
    bands_by_code: dict[str, list[dict]],
    missing_grade: str,
    summary: dict,
    verbose: bool,
) -> list[dict]:
    version = feature_version(cfg)
    neutral_band = float(cfg["state"].get("neutral_band", 0.10))
    persisted: list[dict] = []

    for code, row in state_rows.items():
        prev = db.query_one(
            conn,
            "SELECT * FROM features_daily WHERE code=? AND trade_date<? ORDER BY trade_date DESC LIMIT 1",
            (code, trade_date),
        )
        quality_flag = row.get("quality_flag", "ok")
        if missing_grade == "L3":
            quality_flag = "stale"

        candidates = build_candidates(row, dict(prev) if prev else None, bands_by_code.get(code, []), cfg)
        if quality_flag != "ok":
            candidates.append(_data_anomaly_candidate(code, row, quality_flag, missing_grade))

        risk_veto = any(c.signal_type == "STOP_BREACH" for c in candidates)
        ctx = DecisionContext(
            trade_date=trade_date,
            code=code,
            quality_flag=quality_flag,
            state=row.get("state", "range"),
            risk_veto=risk_veto,
            neutral_band=neutral_band,
        )
        decision = arbitrate(candidates, ctx)
        kept, cooled = apply_cooldown(conn, decision.accepted, cfg, trade_date)
        final, dropped = apply_budget(kept, cfg)

        suppressed_logs = decision.suppressed + cooled + dropped
        written = persist(conn, final, suppressed_logs, trade_date, version)
        if written:
            _log(f"      - {code}: 生成 {written} 条提醒", verbose)
        for item in suppressed_logs:
            summary["issues"].append(
                f"{code} {item['candidate'].signal_type} 被 {item['rule']} 压制：{item['reason']}"
            )
        persisted.extend(
            [
                {
                    "code": code,
                    "level": c.level,
                    "signal_type": c.signal_type,
                    "message": c.message,
                    "arbitrated_by": c.payload.get("arbitrated_by"),
                }
                for c in final
            ]
        )

    # 数据恢复正常的标的，把当日遗留的数据异常提醒撤掉。
    # 否则一次拉数失败留下的 P0 会永远挂在简报和看盘页上，比不提醒还糟。
    healthy = [
        code
        for code, row in state_rows.items()
        if (row.get("quality_flag") or "ok") == "ok" and missing_grade != "L3"
    ]
    if healthy:
        marks = ", ".join("?" for _ in healthy)
        removed = conn.execute(
            f"DELETE FROM alerts WHERE trade_date=? AND signal_type='DATA_ANOMALY' AND code IN ({marks})",
            (trade_date, *healthy),
        ).rowcount
        conn.commit()
        if removed:
            _log(f"      数据已恢复，撤掉 {removed} 条当日数据异常提醒", verbose)
    return persisted


def _data_anomaly_candidate(code: str, row: dict, quality_flag: str, missing_grade: str):
    from .alerts import Candidate

    return Candidate(
        code,
        "DATA_ANOMALY",
        "P0",
        row.get("close"),
        f"数据质量异常（{quality_flag}{'，缺失分级 ' + missing_grade if missing_grade != 'ok' else ''}），已冻结自动提醒",
        row.get("state", ""),
        row.get("opportunity_score", 0.5),
    )


# ---------- 盘中任务 ----------

def collect_instrument(
    conn,
    cfg: dict,
    code: str,
    kind: str = "stock",
    trade_date: str | None = None,
    verbose: bool = False,
) -> dict:
    """给单个标的补齐历史并算好特征、关键带与提醒。

    页面里新加自选时用：只想看一只票，没必要把整个观察池重跑一遍。
    """
    registry = FactorRegistry(cfg.get("factors", []), feature_version(cfg))
    pool = _source_pool(cfg)
    if not pool:
        return {"ok": False, "message": "没有可用数据源"}
    primary, backup = pool[0], pool[1] if len(pool) > 1 else pool[0]
    trade_date = resolve_trade_date(primary, cfg, trade_date)
    start = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=460)).strftime("%Y-%m-%d")

    summary: dict = {"issues": []}
    primary_rows = _fetch(primary, code, start, trade_date, kind, summary)
    backup_rows = _fetch(backup, code, start, trade_date, kind, summary) if backup is not primary else []
    if not primary_rows and not backup_rows:
        return {"ok": False, "message": f"取不到 {code} 的行情：" + ("；".join(summary["issues"]) or "数据源没返回数据")}

    merge = validate.merge_two_sources(primary_rows or backup_rows, backup_rows, cfg)
    checked = validate.validate_bars(merge.rows, cfg)
    for row in checked.rows:
        row["source"] = row.get("source") or primary.name
        row["updated_at"] = db.now_iso()
    written = db.upsert_rows(conn, "bars_daily", checked.rows, ["code", "trade_date"])
    db.upsert_rows(
        conn,
        "instruments",
        [
            {
                "code": code,
                "name": _known_name(conn, code),
                "type": kind,
                "exchange": code[:2],
                "role": "观察",
                "in_watchlist": 1,
                "updated_at": db.now_iso(),
            }
        ],
        ["code"],
    )

    state_rows = _compute_features(conn, cfg, registry, trade_date, verbose, codes=[code])
    bands = _compute_levels(conn, cfg, trade_date, verbose, codes=[code])
    _apply_risk(conn, cfg, trade_date, state_rows, bands, verbose)
    alerts: list[dict] = []
    if state_rows:
        alerts = _decide(conn, cfg, trade_date, state_rows, bands, "ok", {"issues": []}, verbose)
    return {
        "ok": True,
        "code": code,
        "trade_date": trade_date,
        "bars": written,
        "days": len(checked.rows),
        "alerts": alerts,
        "notes": summary["issues"][:3],
    }

def run_intraday(conn, cfg: dict, verbose: bool = True) -> list[dict]:
    """盘中只做两件事：是否跌破支撑带、是否进入支撑带。其余留给日终。"""
    source = _source_for(_source_pool(cfg), "intraday_snapshot")
    if source is None:
        _log("盘中快照不可用：没有支持 intraday_snapshot 的数据源", verbose)
        return []
    codes = [item["code"] for item in watchlist_codes(cfg)]
    try:
        snapshot = source.intraday_snapshot(codes)
    except Exception as exc:
        _log(f"盘中快照不可用：{exc}", verbose)
        return []

    results: list[dict] = []
    for quote in snapshot:
        code, price = quote["code"], quote.get("close")
        if not price:
            continue
        latest = db.query_one(
            conn, "SELECT * FROM features_daily WHERE code=? ORDER BY trade_date DESC LIMIT 1", (code,)
        )
        bands = [
            dict(row)
            for row in db.query(
                conn,
                "SELECT * FROM levels WHERE code=? ORDER BY trade_date DESC LIMIT 8",
                (code,),
            )
        ]
        if latest is None:
            continue

        from .alerts import Candidate

        candidates: list[Candidate] = []
        support = nearest_band(bands, price, "support")
        if support and price < support["price_low"] * 0.995:
            candidates.append(Candidate(code, "STOP_BREACH", "P0", price, f"盘中跌破支撑带 {support['price_low']:.3f}", latest["state"]))
        elif support and abs((price - (support["price_low"] + support["price_high"]) / 2) / price * 100) <= 1.5:
            candidates.append(Candidate(code, "INTRADAY_TOUCH_SUPPORT", "P1", price, "盘中触及支撑带", latest["state"], 0.7, period="intraday"))

        if not candidates:
            continue
        ctx = DecisionContext(
            trade_date=quote.get("dt", "")[:10],
            code=code,
            quality_flag=latest["data_quality_flag"] or "ok",
            state=latest["state"] or "range",
            risk_veto=any(c.signal_type == "STOP_BREACH" for c in candidates),
            neutral_band=float(cfg["state"].get("neutral_band", 0.10)),
        )
        decision = arbitrate(candidates, ctx)
        kept, cooled = apply_cooldown(conn, decision.accepted, cfg, ctx.trade_date)
        final, dropped = apply_budget(kept, cfg)
        persist(conn, final, decision.suppressed + cooled + dropped, ctx.trade_date, feature_version(cfg))
        results.extend([{"code": c.code, "level": c.level, "signal_type": c.signal_type, "message": c.message} for c in final])
    return results
