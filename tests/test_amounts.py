"""成交额比较：昨日/5日/20日、分位，以及覆盖不可比时闭嘴。"""

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from collector import amounts, db

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class AmountCompareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")
        start = date(2026, 1, 1)
        rows = []
        for index in range(25):
            rows.append({
                "trade_date": (start + timedelta(days=index)).isoformat(),
                "total_amount": 100.0 + index * 5,
                "coverage": 5000,
            })
        db.upsert_rows(self.conn, "market_breadth", rows, ["trade_date"])

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_yesterday_five_day_and_twenty_day(self):
        result = amounts.compute(self.conn, {}, "2026-01-26", 250.0, 5000)
        self.assertEqual(result["prev_date"], "2026-01-25")
        self.assertAlmostEqual(result["d1"]["change_pct"], 13.64, places=2)
        self.assertTrue(result["d1"]["comparable"])
        self.assertAlmostEqual(result["windows"]["5"]["change_pct"], 19.05, places=2)
        self.assertAlmostEqual(result["windows"]["20"]["ratio"], 1.449, places=2)
        self.assertEqual(result["windows"]["20"]["label"], "显著放量")
        self.assertEqual(result["history"][-1]["trade_date"], "2026-01-26")

    def test_coverage_gap_marks_not_comparable(self):
        result = amounts.compute(self.conn, {}, "2026-01-26", 250.0, 4000)
        self.assertFalse(result["d1"]["comparable"])
        self.assertFalse(result["windows"]["5"]["comparable"])
        self.assertIn("覆盖差", result["windows"]["5"]["reason"])

    def test_short_history_does_not_fake_a_20_day_baseline(self):
        conn = db.connect(Path(self.tmp.name) / "short.db")
        db.init_db(conn, PROJECT_ROOT / "schema.sql")
        db.upsert_rows(conn, "market_breadth", [
            {"trade_date": "2026-01-01", "total_amount": 100.0, "coverage": 5000},
            {"trade_date": "2026-01-02", "total_amount": 110.0, "coverage": 5000},
        ], ["trade_date"])
        result = amounts.compute(conn, {}, "2026-01-03", 120.0, 5000)
        conn.close()
        self.assertFalse(result["windows"]["20"]["comparable"])
        self.assertIn("至少需要 20", result["windows"]["20"]["reason"])

    def test_baseline_coverage_spread_is_not_comparable(self):
        self.conn.execute(
            "UPDATE market_breadth SET coverage=4000 WHERE trade_date='2026-01-24'")
        self.conn.commit()
        result = amounts.compute(self.conn, {}, "2026-01-26", 250.0, 5000)
        self.assertFalse(result["windows"]["5"]["comparable"])
        self.assertIn("基期覆盖波动", result["windows"]["5"]["reason"])


if __name__ == "__main__":
    unittest.main()
