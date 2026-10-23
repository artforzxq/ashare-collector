"""页面上的"运行任务"：把日终、全市场同步、筛选做成按钮。

为什么放在服务里跑：用户点一下就能用，不用记命令、也不用回去双击 .bat。
代价是要防并发——同一个 SQLite 文件被两个任务同时写会锁库，
所以这里**同一时刻只允许一个任务在跑**，其余请求直接拒绝并说明原因。
"""

from __future__ import annotations

import io
import sys
import threading
import traceback
from pathlib import Path

from . import db

MAX_LOG_LINES = 400
MAX_HISTORY = 8


class Job:
    def __init__(self, key: str, title: str, trigger: str = "manual",
                 scheduled_for: str | None = None):
        self.key = key
        self.title = title
        self.trigger = trigger
        self.scheduled_for = scheduled_for
        self.run_id: int | None = None
        self.status = "running"
        self.started_at = db.now_iso()
        self.finished_at: str | None = None
        self.log: list[str] = []
        self.result: str = ""
        self.error: str = ""
        self._partial = ""

    def add(self, text: str) -> None:
        self._partial += text
        while "\n" in self._partial:
            line, _, self._partial = self._partial.partition("\n")
            line = line.rstrip()
            if line:
                self.log.append(line)
        if len(self.log) > MAX_LOG_LINES:
            del self.log[: len(self.log) - MAX_LOG_LINES]

    def tail(self, count: int = 12) -> list[str]:
        return self.log[-count:]

    def to_dict(self, log_lines: int = 12) -> dict:
        return {
            "key": self.key,
            "title": self.title,
            "run_id": self.run_id,
            "trigger": self.trigger,
            "scheduled_for": self.scheduled_for,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
            "log": self.tail(log_lines),
            "log_lines": len(self.log),
        }


class _Stream(io.TextIOBase):
    """把任务里的 print 收进任务的日志，顺便仍然打到服务端终端。"""

    def __init__(self, job: Job, mirror):
        self.job = job
        self.mirror = mirror

    def write(self, text: str) -> int:
        self.job.add(text)
        try:
            self.mirror.write(text)
        except Exception:
            pass
        return len(text)

    def flush(self) -> None:
        try:
            self.mirror.flush()
        except Exception:
            pass


class JobManager:
    def __init__(self, db_path: str | Path | None = None,
                 schema_path: str | Path | None = None):
        self._lock = threading.Lock()
        self._current: Job | None = None
        self._history: list[Job] = []
        self._db_path = db_path
        self._schema_path = schema_path
        self._storage_ready = False
        self._ensure_storage()

    def _connection(self):
        if not self._db_path:
            return None
        return db.connect(self._db_path)

    def _ensure_storage(self) -> None:
        if not self._db_path or self._storage_ready:
            return
        conn = self._connection()
        try:
            if not db.table_columns(conn, "job_runs"):
                if self._schema_path:
                    db.init_db(conn, self._schema_path)
                else:
                    return
            # 进程重启后，上次没来得及收尾的 running 行没有真实执行者了。
            conn.execute(
                """UPDATE job_runs SET status='interrupted',
                       finished_at=COALESCE(finished_at, ?),
                       error=COALESCE(error, '服务重启或进程中断')
                     WHERE status='running'""",
                (db.now_iso(),),
            )
            conn.commit()
            self._storage_ready = True
        except Exception:
            # 任务执行本身比执行台账重要：库暂时不可写也不能阻止页面运行任务。
            self._storage_ready = False
        finally:
            conn.close()

    @staticmethod
    def _row_to_dict(row) -> dict:
        return {
            "key": row["job_key"], "title": row["title"], "run_id": row["id"],
            "trigger": row["trigger"], "scheduled_for": row["scheduled_for"],
            "status": row["status"], "started_at": row["started_at"],
            "finished_at": row["finished_at"], "result": row["result"] or "",
            "error": row["error"] or "", "log": [],
        }

    def _insert_start(self, job: Job) -> None:
        if not self._db_path:
            return
        self._ensure_storage()
        if not self._storage_ready:
            return
        conn = self._connection()
        try:
            cursor = conn.execute(
                """INSERT INTO job_runs
                       (job_key, title, trigger, scheduled_for, status, started_at,
                        result, error, log_tail, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (job.key, job.title, job.trigger, job.scheduled_for, "running",
                 job.started_at, "", "", "", db.now_iso()),
            )
            conn.commit()
            job.run_id = cursor.lastrowid
        finally:
            conn.close()

    def _persist_finish(self, job: Job) -> None:
        if not self._db_path:
            return
        self._ensure_storage()
        if not self._storage_ready or not job.run_id:
            return
        conn = self._connection()
        try:
            conn.execute(
                """UPDATE job_runs SET status=?, finished_at=?, result=?, error=?,
                       log_tail=? WHERE id=?""",
                (job.status, job.finished_at, job.result[:300], job.error[:500],
                 "\n".join(job.tail(20)), job.run_id),
            )
            conn.commit()
        finally:
            conn.close()

    # ---- 查询 ----

    def state(self, log_lines: int = 12) -> dict:
        with self._lock:
            current = self._current
            if self._db_path:
                rows = []
                conn = self._connection()
                try:
                    rows = [self._row_to_dict(row) for row in db.query(
                        conn,
                        """SELECT * FROM job_runs ORDER BY started_at DESC, id DESC LIMIT 30""",
                    )]
                except Exception:
                    rows = []
                finally:
                    conn.close()
                if rows:
                    return {
                        "running": current.to_dict(log_lines) if current else None,
                        "recent": rows[:4],
                        "history": rows,
                    }
            return {
                "running": current.to_dict(log_lines) if current else None,
                "recent": [job.to_dict(4) for job in self._history[-4:]][::-1],
                "history": [job.to_dict(4) for job in self._history[::-1]],
            }

    # ---- 启动 ----

    def start(self, key: str, title: str, func, trigger: str = "manual",
              scheduled_for: str | None = None) -> dict:
        with self._lock:
            if self._current is not None:
                return {"ok": False,
                        "message": f"「{self._current.title}」还在跑，等它结束再开始下一个"}
            job = Job(key, title, trigger=trigger, scheduled_for=scheduled_for)
            self._current = job
        self._insert_start(job)
        threading.Thread(target=self._worker, args=(job, func), daemon=True).start()
        return {"ok": True, "job": job.to_dict()}

    def _worker(self, job: Job, func) -> None:
        original = sys.stdout
        sys.stdout = _Stream(job, original)
        try:
            outcome = func()
            job.result = str(outcome)[:300] if outcome else ""
            job.status = "done"
        except Exception as exc:                       # 任务里任何异常都不能带塌服务
            job.error = f"{type(exc).__name__}: {exc}"
            job.log.append(traceback.format_exc()[-600:])
            job.status = "failed"
        finally:
            sys.stdout = original
            job.finished_at = db.now_iso()
            # 先落库，再清 current：否则轮询可能刚好看到“current 已空、
            # 但历史还是 running”这一瞬间，页面会短暂显示任务没结束。
            self._persist_finish(job)
            with self._lock:
                self._current = None
                self._history.append(job)
                if len(self._history) > MAX_HISTORY:
                    del self._history[: len(self._history) - MAX_HISTORY]
