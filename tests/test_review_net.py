"""提醒复盘：净收益与基准必须走同一笔成本，超额不能被扣两次。"""

import tempfile
import unittest
from pathlib import Path

from collector import db, review

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ReviewNetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")
        days = [f"2026-09-{index:02d}" for index in range(1, 26)]
        rows = []
        for index, day in enumerate(days):
            rows.append({"code": "SH600000", "trade_date": day, "close": 100.0 + index})
            rows.append({"code": "SH000300", "trade_date": day, "close": 200.0 + index * 0.5})
        db.upsert_rows(self.conn, "bars_daily", rows, ["code", "trade_date"])
        db.upsert_rows(self.conn, "alerts", [{
            "created_at": "2026-09-01 15:00:00", "trade_date": days[0],
            "code": "SH600000", "level": "P1", "signal_type": "STATE_TO_UP",
            "outcome_20d": 5.0,
        }], ["code", "trade_date", "signal_type", "level"])

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_excess_is_gross_benchmark_relative(self):
        rows = review.benchmark_stats(self.conn, cost=0.302)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertAlmostEqual(row["net20"], 4.698, places=2)
        # 基准也扣同一笔成本 → 超额 = 信号毛收益 − 基准毛收益
        series, bars = review.load_series(self.conn, "SH000300", {})
        bench = review.forward_return(bars, series, "2026-09-01", 20)
        self.assertAlmostEqual(row["excess20"], 5.0 - bench, places=2)


if __name__ == "__main__":
    unittest.main()
