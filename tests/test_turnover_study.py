"""换手率研究：分档、超额、以及"别把噪声当结论"。

它和状态机回测是两个问题——这个不经过状态机，直接看"放量之后怎么了"。
所以测试重点在**分档口径**和**超额怎么算**（只减基准，不能只看均值）。
"""

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from collector import backtest, db
from collector.config import load_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class BucketTests(unittest.TestCase):
    def test_bucket_boundaries(self):
        self.assertEqual(backtest._bucket_of(0.5), "缩量 <0.8")
        self.assertEqual(backtest._bucket_of(0.8), "常态 0.8–1.2")
        self.assertEqual(backtest._bucket_of(1.19), "常态 0.8–1.2")
        self.assertEqual(backtest._bucket_of(1.2), "温和放量 1.2–2")
        self.assertEqual(backtest._bucket_of(2.0), "明显放量 2–3")
        self.assertEqual(backtest._bucket_of(3.0), "暴力放量 >3")
        self.assertEqual(backtest._bucket_of(99.0), "暴力放量 >3")


class TurnoverStudyTests(unittest.TestCase):
    """用合成日线跑一遍完整流程（离线，夹具源自己带换手率）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=self.root)
        self.cfg["_db_path"] = str(self.root / "t.db")
        self.cfg["backtest"] = {"universe": "watchlist", "min_bars": 60, "cost": {
            "commission_bps": 2.5, "stamp_bps": 5.0, "transfer_bps": 0.1}}
        self.conn = db.connect(self.cfg["_db_path"])
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _seed(self, code="SH600000", days=120):
        """一段平稳上涨的日线，换手率在**中段**放大——分档应该能把它挑出来。

        放量段刻意放在中段：贴在序列末尾的话，那几天没有 20 日前瞻收益，
        会被正确地丢掉（没有未来函数），测试就看不到样本了。
        """
        start = date(2026, 1, 5)
        rows = []
        for index in range(days):
            close = 10.0 * (1 + index * 0.002)
            turnover = 12.0 if 70 <= index < 90 else 3.0   # 中段 20 天换手翻了 4 倍
            rows.append({
                "code": code,
                "trade_date": (start + timedelta(days=index)).isoformat(),
                "open": close * 0.995, "high": close * 1.01, "low": close * 0.99,
                "close": close, "pre_close": close * 0.998, "pct_chg": 0.2,
                "volume": 1_000_000.0, "amount": 1e8, "turnover_rate": turnover,
                "quality_flag": "ok",
            })
        db.upsert_rows(self.conn, "bars_daily", rows, ["code", "trade_date"])
        db.upsert_rows(self.conn, "instruments",
                       [{"code": code, "name": "示例", "type": "stock"}], ["code"])
        self.cfg["watchlist"] = {"indices": [], "etfs": [], "stocks": [code]}

    def test_study_buckets_the_spike_days(self):
        self._seed()
        result = backtest.turnover_study(self.conn, self.cfg, mode="watchlist", verbose=False)
        self.assertTrue(result["ok"])
        buckets = {entry["bucket"]: entry for entry in result["buckets"]}
        # 前 100 天换手 3%，后 20 天换手 12%（4 倍）→ 只有最后几天该落进"暴力放量"
        self.assertGreater(buckets["常态 0.8–1.2"]["n20"], 0)
        self.assertGreater(buckets["暴力放量 >3"]["n20"], 0)
        self.assertLess(buckets["暴力放量 >3"]["n20"], buckets["常态 0.8–1.2"]["n20"])

    def test_excess_is_measured_against_the_baseline(self):
        """均值只说明涨没涨，超额才说明"比随便买一只强不强"。"""
        self._seed()
        result = backtest.turnover_study(self.conn, self.cfg, mode="watchlist", verbose=False)
        for entry in result["buckets"]:
            if entry["avg20"] is None:
                continue
            self.assertIsNotNone(entry["base20"])          # 基准必须先算出来
            self.assertAlmostEqual(entry["excess20"], entry["avg20"] - entry["base20"], places=4)

    def test_missing_turnover_is_counted_not_silently_dropped(self):
        self._seed()
        db.query(  # 把一半的换手率抹掉
            self.conn, "UPDATE bars_daily SET turnover_rate=NULL WHERE trade_date LIKE '%1%'")
        result = backtest.turnover_study(self.conn, self.cfg, mode="watchlist", verbose=False)
        self.assertGreater(result["missing_turnover"], 0)
        self.assertGreater(result["covered"], 0)

    def test_report_says_how_to_read_it(self):
        self._seed()
        result = backtest.turnover_study(self.conn, self.cfg, mode="watchlist", verbose=False)
        text = backtest.render_turnover_study(result)
        self.assertIn("换手率与之后的收益", text)
        self.assertIn("超额", text)
        self.assertIn("单调性", text)

    def test_result_can_be_saved_and_read_back(self):
        self._seed()
        result = backtest.turnover_study(self.conn, self.cfg, mode="watchlist", verbose=False)
        saved = backtest.save_turnover(result, self.root)
        self.assertTrue(saved.exists())
        latest = backtest.latest_turnover(self.root)
        self.assertEqual(len(latest["buckets"]), len(result["buckets"]))
        self.assertIsNotNone(latest["_saved_at"])

    def test_no_turnover_data_at_all_is_not_a_crash(self):
        self._seed()
        db.query(self.conn, "UPDATE bars_daily SET turnover_rate=NULL")
        result = backtest.turnover_study(self.conn, self.cfg, mode="watchlist", verbose=False)
        self.assertTrue(result["ok"])
        self.assertEqual(result["covered"], 0)
        self.assertTrue(all(entry["avg20"] is None for entry in result["buckets"]))


if __name__ == "__main__":
    unittest.main()
