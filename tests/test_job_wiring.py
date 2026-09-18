"""页面上的每个任务按钮都必须真的能跑起来。

这里不测业务逻辑（那是各自的测试），只测**接线**：任务函数里用到的模块、名字在不在作用域。
真有写错的名字时，JobManager 会把 NameError 记进任务日志，所以看任务状态就够了——
「同步全市场」按钮曾经就是这样坏的：`warehouse_mod` 只在别的函数里局部导入过。
"""

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from collector import db, screen, server, tasks, warehouse
from collector.config import load_config, use_fixture_sources

PROJECT_ROOT = Path(__file__).resolve().parents[1]
JOB_KEYS = ("daily", "sync", "screen", "snapshot", "intraday")


class JobWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=root)
        use_fixture_sources(self.cfg)
        self.cfg["_db_path"] = str(root / "t.db")
        conn = db.connect(self.cfg["_db_path"])
        db.init_db(conn, PROJECT_ROOT / "schema.sql")
        conn.close()
        self.app = server.App(self.cfg)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, key: str) -> dict:
        self.app.run_job(key)
        deadline = time.time() + 10
        while time.time() < deadline:
            state = self.app.job_state()
            if not state["running"] and state["recent"]:
                return state["recent"][0]
            time.sleep(0.05)
        self.fail(f"{key} 十秒内没跑完")

    def test_every_job_button_is_wired(self):
        empty_scan = {"ok": True, "trade_date": "2026-01-05", "with_data": 0, "groups": {}, "criteria": []}
        patches = [
            mock.patch.object(warehouse, "sync_universe", lambda *a, **k: None),
            mock.patch.object(warehouse, "sync_history",
                              lambda *a, **k: {"ok": True, "written": 0, "remaining": 0}),
            mock.patch.object(warehouse, "snapshot_bars",
                              lambda *a, **k: {"ok": True, "written": 0}),
            mock.patch.object(screen, "scan", lambda *a, **k: empty_scan),
            mock.patch.object(screen, "sync_criteria", lambda *a, **k: 0),
            mock.patch.object(screen, "save", lambda *a, **k: 0),
            mock.patch.object(tasks, "run_daily",
                              lambda *a, **k: {"trade_date": "2026-01-05", "alerts": []}),
            mock.patch.object(tasks, "collect_intraday_bars",
                              lambda *a, **k: {"ok": True, "codes": 0, "bars": 0}),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

        for key in JOB_KEYS:
            job = self._run(key)
            self.assertEqual(job["key"], key)
            self.assertEqual(job["status"], "done", f"{key} 失败：{job.get('log')}")

    def test_unknown_key_is_rejected(self):
        result = self.app.run_job("nope")
        self.assertFalse(result["ok"])
        self.assertIn("未知任务", result["message"])


if __name__ == "__main__":
    unittest.main()
