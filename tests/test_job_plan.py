"""任务计划：按时完成、待执行、依赖阻塞与逾期。"""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from collector import db, job_plan

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class JobPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")
        db.upsert_rows(self.conn, "trade_calendar", [
            {"trade_date": day, "is_trading_day": 1}
            for day in ("2026-10-20", "2026-10-21", "2026-10-22")
        ], ["trade_date"])
        db.upsert_rows(self.conn, "features_daily", [
            {"code": "SH600000", "trade_date": "2026-10-22", "state": "range"}
        ], ["code", "trade_date"])

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _run(self, key, status, finished):
        self.conn.execute(
            """INSERT INTO job_runs
                   (job_key, title, status, started_at, finished_at, created_at)
               VALUES (?,?,?,?,?,?)""",
            (key, key, status, finished, finished, finished),
        )
        self.conn.commit()

    def test_done_overdue_and_data_asof(self):
        self._run("daily", "done", "2026-10-22 15:40:00")
        plan = job_plan.build(
            self.conn, {}, now=datetime(2026, 10, 22, 16, 30))
        jobs = {item["key"]: item for item in plan["jobs"]}
        self.assertEqual(jobs["daily"]["status"], "done")
        self.assertEqual(jobs["daily"]["data_asof"], "2026-10-22")
        self.assertEqual(jobs["screen"]["status"], "overdue")
        self.assertEqual(jobs["push"]["status"], "overdue")
        self.assertGreaterEqual(plan["summary"]["overdue"], 2)
        self.assertTrue(plan["schedule"])

    def test_before_due_time_is_pending(self):
        self.conn.execute("DELETE FROM features_daily")
        self.conn.commit()
        plan = job_plan.build(
            self.conn, {}, now=datetime(2026, 10, 22, 15, 0))
        jobs = {item["key"]: item for item in plan["jobs"]}
        self.assertEqual(jobs["daily"]["status"], "pending")
        self.assertEqual(jobs["screen"]["status"], "pending")

    def test_data_freshness_proves_cli_daily_run(self):
        plan = job_plan.build(
            self.conn, {}, now=datetime(2026, 10, 22, 16, 30))
        jobs = {item["key"]: item for item in plan["jobs"]}
        self.assertEqual(jobs["daily"]["status"], "done")
        self.assertEqual(jobs["daily"]["success_evidence"], "data")
        self.assertEqual(jobs["screen"]["status"], "overdue")

    def test_missing_dependency_blocks_downstream(self):
        self.conn.execute("DELETE FROM features_daily")
        self.conn.commit()
        plan = job_plan.build(
            self.conn, {}, now=datetime(2026, 10, 22, 16, 30))
        jobs = {item["key"]: item for item in plan["jobs"]}
        self.assertEqual(jobs["daily"]["status"], "overdue")
        self.assertEqual(jobs["screen"]["status"], "blocked")
        self.assertEqual(jobs["push"]["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
