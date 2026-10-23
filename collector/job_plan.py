"""任务计划与逾期判断：区分"没到点、该跑没跑、失败、成功但无新数据"。

任务中心的按钮只是动作；这里补的是**计划**：
  - 每日任务：每个交易日 15:30 后应成功一次；
  - 全市场筛选：依赖每日任务，16:00 后应成功一次；
  - 推送简报：依赖每日任务，15:40 后应成功一次；
  - 其余任务（同步、快照、分时、回测、新股、AI）是手动/按需，不制造逾期告警。

判断依据是 `job_runs`（执行事实）+ 数据表最新日期（结果事实）。
任务返回成功不等于数据更新了，两个日期都显示出来。
"""

from __future__ import annotations

from datetime import datetime

from . import db

DEFAULTS = {
    "daily": {"title": "每日任务", "cadence": "trading_day", "after": "15:30", "depends_on": []},
    "screen": {"title": "全市场筛选", "cadence": "trading_day", "after": "16:00", "depends_on": ["daily"]},
    "push": {"title": "推送简报", "cadence": "trading_day", "after": "15:40", "depends_on": ["daily"]},
    "sync": {"title": "同步全市场", "cadence": "manual", "depends_on": []},
    "snapshot": {"title": "补当日快照", "cadence": "manual", "depends_on": []},
    "intraday": {"title": "抓当日分时", "cadence": "manual", "depends_on": []},
    "backtest": {"title": "参数回测", "cadence": "manual", "depends_on": []},
    "listings": {"title": "刷新新股判定", "cadence": "manual", "depends_on": []},
}


def settings(cfg: dict | None = None) -> dict:
    raw = (cfg or {}).get("job_plan") or {}
    out = {key: dict(value) for key, value in DEFAULTS.items()}
    for key, value in raw.items():
        if key in out and isinstance(value, dict):
            out[key].update(value)
    return out


def reference_day(conn) -> str | None:
    today = db.now_iso()[:10]
    row = db.query_one(
        conn,
        """SELECT MAX(trade_date) AS d FROM trade_calendar
            WHERE is_trading_day=1 AND trade_date<=?""",
        (today,),
    )
    if row and row["d"]:
        return row["d"]
    row = db.query_one(conn, "SELECT MAX(trade_date) AS d FROM bars_daily WHERE trade_date<=?", (today,))
    return row["d"] if row and row["d"] else today


def _latest(conn, sql: str, params: tuple = ()) -> str | None:
    try:
        row = db.query_one(conn, sql, params)
        return row["d"] if row and row["d"] else None
    except Exception:
        return None


def data_asof(conn, key: str) -> str | None:
    queries = {
        "daily": "SELECT MAX(trade_date) AS d FROM features_daily",
        "screen": "SELECT MAX(trade_date) AS d FROM screen_results",
        "sync": "SELECT MAX(trade_date) AS d FROM bars_daily",
        "snapshot": "SELECT MAX(trade_date) AS d FROM bars_daily WHERE source='snapshot'",
        "intraday": "SELECT MAX(trade_date) AS d FROM bars_intraday",
        "listings": "SELECT MAX(last_seen) AS d FROM new_listings",
    }
    sql = queries.get(key)
    return _latest(conn, sql) if sql else None


def _runs(conn, limit: int = 200) -> list[dict]:
    try:
        return [
            dict(row)
            for row in db.query(
                conn,
                "SELECT * FROM job_runs ORDER BY started_at DESC, id DESC LIMIT ?",
                (limit,),
            )
        ]
    except Exception:
        return []


def _day_of(value: str | None) -> str | None:
    return value[:10] if value else None


def _success_by_day(runs: list[dict]) -> dict[tuple[str, str], dict]:
    out = {}
    for row in runs:
        if row.get("status") != "done":
            continue
        day = _day_of(row.get("finished_at") or row.get("started_at"))
        if day:
            out.setdefault((row["job_key"], day), row)
    return out


def _data_days(conn, key: str) -> set[str]:
    """CLI/启动器执行没有 job_runs，但数据本身能证明任务做过。"""
    sql = {
        "daily": "SELECT DISTINCT trade_date FROM features_daily",
        "screen": "SELECT DISTINCT trade_date FROM screen_results",
        "sync": "SELECT DISTINCT trade_date FROM bars_daily",
    }.get(key)
    if not sql:
        return set()
    try:
        return {row["trade_date"] for row in db.query(conn, sql)}
    except Exception:
        return set()


def _trading_days(conn, reference: str, count: int = 7) -> list[str]:
    rows = [
        row["trade_date"]
        for row in db.query(
            conn,
            """SELECT trade_date FROM trade_calendar
                WHERE is_trading_day=1 AND trade_date<=?
                ORDER BY trade_date DESC LIMIT ?""",
            (reference, count),
        )
    ]
    if rows:
        return list(reversed(rows))
    rows = [
        row["trade_date"]
        for row in db.query(
            conn,
            """SELECT DISTINCT trade_date FROM bars_daily
                WHERE trade_date<=? ORDER BY trade_date DESC LIMIT ?""",
            (reference, count),
        )
    ]
    return list(reversed(rows))


def build(conn, cfg: dict | None = None, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    plan = settings(cfg)
    reference = reference_day(conn) or now.strftime("%Y-%m-%d")
    runs = _runs(conn)
    successes = _success_by_day(runs)
    data_days = {key: _data_days(conn, key) for key in plan}
    last_success: dict[str, dict] = {}
    last_failure: dict[str, dict] = {}
    last_any: dict[str, dict] = {}
    for row in runs:
        key = row["job_key"]
        last_any.setdefault(key, row)
        if row.get("status") == "done":
            last_success.setdefault(key, row)
        elif row.get("status") in ("failed", "interrupted"):
            last_failure.setdefault(key, row)

    def status_for(key: str, spec: dict, day: str) -> str:
        if spec.get("cadence") != "trading_day":
            return "manual"
        success = successes.get((key, day))
        if success or day in data_days.get(key, set()):
            return "done"
        missing_dep = [
            dep for dep in spec.get("depends_on") or []
            if plan.get(dep, {}).get("cadence") == "trading_day"
            and not (successes.get((dep, day)) or day in data_days.get(dep, set()))
        ]
        if missing_dep:
            if day == reference and now < datetime.strptime(f"{day} {spec.get('after', '15:30')}:00",
                                                           "%Y-%m-%d %H:%M:%S"):
                return "pending"
            return "blocked"
        expected = datetime.strptime(f"{day} {spec.get('after', '15:30')}:00", "%Y-%m-%d %H:%M:%S")
        if day == reference and now < expected:
            return "pending"
        return "overdue"

    jobs = []
    for key, spec in plan.items():
        state = status_for(key, spec, reference)
        run_success = successes.get((key, reference))
        evidence = (
            "job_run" if run_success
            else "data" if reference in data_days.get(key, set())
            else None
        )
        jobs.append({
            "key": key,
            "title": spec.get("title") or key,
            "cadence": spec.get("cadence"),
            "after": spec.get("after"),
            "depends_on": spec.get("depends_on") or [],
            "status": state,
            "expected_for": (
                f"{reference} {spec.get('after')}:00" if spec.get("cadence") == "trading_day" else None
            ),
            "last_success": last_success.get(key),
            "last_failure": last_failure.get(key),
            "last_run": last_any.get(key),
            "data_asof": data_asof(conn, key),
            "success_evidence": evidence,
        })

    days = _trading_days(conn, reference, 7)
    schedule = []
    for day in days:
        for key, spec in plan.items():
            if spec.get("cadence") != "trading_day":
                continue
            state = status_for(key, spec, day)
            success = successes.get((key, day))
            inferred = day in data_days.get(key, set())
            schedule.append({
                "date": day,
                "key": key,
                "title": spec.get("title") or key,
                "expected_for": f"{day} {spec.get('after')}:00",
                "status": "done" if (success or inferred) else state,
                "run_id": success.get("id") if success else None,
                "finished_at": success.get("finished_at") if success else None,
                "data_asof": data_asof(conn, key) if day == reference else None,
                "evidence": "job_run" if success else "data" if inferred else None,
            })
    schedule.sort(key=lambda item: (item["date"], item["expected_for"]), reverse=True)
    counts: dict[str, int] = {}
    for job in jobs:
        counts[job["status"]] = counts.get(job["status"], 0) + 1
    return {
        "reference_day": reference,
        "now": now.strftime("%Y-%m-%d %H:%M:%S"),
        "jobs": jobs,
        "schedule": schedule,
        "summary": {
            "overdue": counts.get("overdue", 0),
            "pending": counts.get("pending", 0),
            "done": counts.get("done", 0),
            "blocked": counts.get("blocked", 0),
            "failed": sum(1 for job in jobs if job["last_failure"] and job["status"] != "done"),
            "manual": counts.get("manual", 0),
        },
    }
