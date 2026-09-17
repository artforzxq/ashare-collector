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
from datetime import datetime

MAX_LOG_LINES = 400
MAX_HISTORY = 8


class Job:
    def __init__(self, key: str, title: str):
        self.key = key
        self.title = title
        self.status = "running"
        self.started_at = datetime.now().strftime("%H:%M:%S")
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
    def __init__(self):
        self._lock = threading.Lock()
        self._current: Job | None = None
        self._history: list[Job] = []

    # ---- 查询 ----

    def state(self, log_lines: int = 12) -> dict:
        with self._lock:
            current = self._current
            return {
                "running": current.to_dict(log_lines) if current else None,
                "recent": [job.to_dict(4) for job in self._history[-4:]][::-1],
            }

    # ---- 启动 ----

    def start(self, key: str, title: str, func) -> dict:
        with self._lock:
            if self._current is not None:
                return {"ok": False,
                        "message": f"「{self._current.title}」还在跑，等它结束再开始下一个"}
            job = Job(key, title)
            self._current = job
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
            job.finished_at = datetime.now().strftime("%H:%M:%S")
            with self._lock:
                self._current = None
                self._history.append(job)
                if len(self._history) > MAX_HISTORY:
                    del self._history[: len(self._history) - MAX_HISTORY]
