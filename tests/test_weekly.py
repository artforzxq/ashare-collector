"""周卡推送：只在阶段/主线变化时说话。"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from collector import db, sentiment, weekly

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class WeeklyPushTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = {
            "_db_path": str(self.root / "t.db"),
            "_project_root": str(self.root),
            "notify": {"weekly_state_file": "weekly-state.json"},
        }
        self.conn = db.connect(self.cfg["_db_path"])
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")
        rows = [
            {"trade_date": f"2026-01-{index:02d}", "coverage": 5000,
             "max_boards": 3, "limit_up_count": 40, "limit_down_count": 5,
             "broken_limit_count": 12, "up_ratio": 0.5, "total_amount": 1e12}
            for index in range(1, 7)
        ]
        db.upsert_rows(self.conn, "market_breadth", rows, ["trade_date"])
        sentiment.backfill(self.conn, {})

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_first_push_then_silence_then_stage_change(self):
        with mock.patch("collector.weekly.notify.send", return_value={"ok": True}) as send:
            first = weekly.push_if_changed(self.conn, self.cfg)
            self.assertTrue(first["sent"])
            self.assertEqual(send.call_count, 1)
            second = weekly.push_if_changed(self.conn, self.cfg)
            self.assertFalse(second["sent"])
            self.assertIn("无变化", second["reason"])
            self.assertEqual(send.call_count, 1)

            self.conn.execute(
                "UPDATE market_breadth SET sentiment_stage='高潮' WHERE trade_date='2026-01-06'")
            self.conn.commit()
            third = weekly.push_if_changed(self.conn, self.cfg)
            self.assertTrue(third["sent"])
            self.assertTrue(any("情绪阶段" in reason for reason in third["reasons"]))
            self.assertEqual(send.call_count, 2)

    def test_dry_run_does_not_write_state(self):
        result = weekly.push_if_changed(self.conn, self.cfg, dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertFalse((self.root / "weekly-state.json").exists())


if __name__ == "__main__":
    unittest.main()
