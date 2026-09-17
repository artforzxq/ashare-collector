"""SQLite 访问层：建库、幂等写入、数据体检记录。

所有写入按主键 upsert，任何任务都可以全量重跑而不产生重复数据。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection, schema_path: str | Path) -> None:
    sql = Path(schema_path).read_text(encoding="utf-8")
    conn.executescript(sql)
    apply_migrations(conn)
    conn.commit()


# 老库升级用：schema.sql 里是 CREATE TABLE IF NOT EXISTS，已经存在的表不会再加字段，
# 所以新增列要在这里补一次（SQLite 支持 ADD COLUMN）。
MIGRATIONS: dict[str, dict[str, str]] = {
    "features_daily": {
        "position_cap": "REAL",
        "stop_level": "REAL",
        "risk_reward": "REAL",
        "candle_pattern": "TEXT",
        "risk_note": "TEXT",
    },
}


def apply_migrations(conn: sqlite3.Connection) -> list[str]:
    """给已存在的表补新字段，返回这次真正加上的字段。"""
    added: list[str] = []
    for table, columns in MIGRATIONS.items():
        existing = set(table_columns(conn, table))
        if not existing:
            continue
        for name, ddl in columns.items():
            if name in existing:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
            added.append(f"{table}.{name}")
    if added:
        conn.commit()
    return added


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r["name"] for r in rows]


def upsert_rows(
    conn: sqlite3.Connection,
    table: str,
    rows: Iterable[dict],
    conflict_cols: Sequence[str],
) -> int:
    """按 conflict_cols 幂等写入。返回实际处理的记录数。"""
    payload = [dict(r) for r in rows if r]
    if not payload:
        return 0

    valid_cols = set(table_columns(conn, table))
    if not valid_cols:
        raise ValueError(f"{table}: 表不存在（先跑一次 init-db 或任意命令让建表脚本执行）")
    cols = [c for c in payload[0].keys() if c in valid_cols]
    if not cols:
        raise ValueError(f"{table}: 没有可写入的字段")

    update_cols = [c for c in cols if c not in conflict_cols]
    placeholders = ", ".join("?" for _ in cols)
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
    if update_cols:
        setters = ", ".join(f"{c}=excluded.{c}" for c in update_cols)
        sql += f" ON CONFLICT({', '.join(conflict_cols)}) DO UPDATE SET {setters}"
    else:
        sql += f" ON CONFLICT({', '.join(conflict_cols)}) DO NOTHING"

    conn.executemany(sql, [[r.get(c) for c in cols] for r in payload])
    conn.commit()
    return len(payload)


def query(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def query_one(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
    return conn.execute(sql, params).fetchone()


def log_health(
    conn: sqlite3.Connection,
    run_date: str,
    source: str,
    task: str,
    status: str,
    rows: int = 0,
    missing_rate: float = 0.0,
    latency_ms: int = 0,
    error_msg: str | None = None,
) -> None:
    upsert_rows(
        conn,
        "data_health",
        [
            {
                "run_date": run_date,
                "source": source,
                "task": task,
                "status": status,
                "rows": rows,
                "missing_rate": missing_rate,
                "latency_ms": latency_ms,
                "error_msg": error_msg,
                "created_at": now_iso(),
            }
        ],
        ["run_date", "source", "task"],
    )


def dump_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)
