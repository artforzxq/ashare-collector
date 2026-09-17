"""页面任务执行器：跑得完、能防并发、失败要留证据。"""

import tempfile
import time
import unittest
from pathlib import Path

from collector import db, jobs, server
from collector.config import load_config
from tests.test_server import CONFIG_SAMPLE, _seed_db


def _wait(manager, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = manager.state()
        if not state["running"]:
            return state
        time.sleep(0.02)
    raise AssertionError("任务没有在预期时间内结束")


class JobManagerTests(unittest.TestCase):
    def test_successful_job_captures_output(self):
        manager = jobs.JobManager()
        result = manager.start("demo", "演示任务", lambda: (print("第一行"), print("第二行"), "完成")[-1])
        self.assertTrue(result["ok"])
        state = _wait(manager)
        last = state["recent"][0]
        self.assertEqual(last["status"], "done")
        self.assertIn("第一行", last["log"])
        self.assertEqual(last["result"], "完成")

    def test_failure_is_recorded_not_raised(self):
        manager = jobs.JobManager()

        def boom():
            raise ValueError("炸了")

        manager.start("demo", "会失败的任务", boom)
        last = _wait(manager)["recent"][0]
        self.assertEqual(last["status"], "failed")
        self.assertIn("炸了", last["error"])

    def test_second_job_is_rejected_while_running(self):
        manager = jobs.JobManager()

        def slow():
            time.sleep(0.3)
            return "ok"

        self.assertTrue(manager.start("a", "慢任务", slow)["ok"])
        second = manager.start("b", "插队的任务", lambda: "x")
        self.assertFalse(second["ok"])
        self.assertIn("还在跑", second["message"])
        _wait(manager)
        self.assertTrue(manager.start("c", "下一个任务", lambda: "y")["ok"])   # 结束后可以再跑


class JobRouteTests(unittest.TestCase):
    """走 HTTP 路由那一层：页面按钮实际打的就是这两个接口。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cfg_path = root / "config.yaml"
        cfg_path.write_text(CONFIG_SAMPLE, encoding="utf-8")
        cls.cfg = load_config(cfg_path, project_root=root)
        cls.cfg["_db_path"] = str(root / "market.db")
        cls.cfg["_config_path"] = str(cfg_path)
        _seed_db(Path(cls.cfg["_db_path"]))
        cls.app = server.App(cls.cfg)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_unknown_job_is_rejected(self):
        import json

        status, _, body = server.dispatch(self.app, "POST", "/api/jobs", json.dumps({"job": "nope"}).encode())
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertFalse(payload["ok"])
        self.assertIn("未知任务", payload["message"])

    def test_jobs_endpoint_reports_state(self):
        import json

        status, _, body = server.dispatch(self.app, "GET", "/api/jobs")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertIn("running", payload)
        self.assertIn("recent", payload)


if __name__ == "__main__":
    unittest.main()
