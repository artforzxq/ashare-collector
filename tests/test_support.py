"""疑似托底：份额净流入 + 异常放量，两条同时成立才算命中。

重点测三件事：阈值卡得准、只描述事实不猜、留痕（support_days + alerts）只写有命中的日子。
份额是 T+1 披露的，所以这里的场景都是"事后"：昨天申购了，今天才看得到。
"""

import tempfile
import unittest
from pathlib import Path

from collector import db, support
from collector.config import load_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CODE = "SH510300"


class SupportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=self.root)
        self.cfg["_db_path"] = str(self.root / "t.db")
        self.cfg["support"] = {
            "etfs": [CODE],
            "shares_pct": 0.01,
            "amount_z": 2.0,
            "amount_ratio": 2.0,
            "min_inflow": 200_000_000,
            "multi_count": 3,
            "history_days": 60,
        }
        self.conn = db.connect(self.cfg["_db_path"])
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _seed(self, code=CODE, shares_prev=10_000_000_000.0, shares_today=10_200_000_000.0,
              amounts=None, nav=4.0, close=4.0):
        """造一段日线 + 两天份额。

        默认就是"份额 +2%、成交额 5 倍放大"的标准托底日：20 个平静日 + 当天放量。
        日期必须连续且不重复——用假日期生成容易把当天覆盖掉，所以这里老老实实往前推。
        """
        from datetime import date, timedelta

        amounts = amounts or [1e8] * 20 + [5e8]
        end = date(2026, 9, 18)
        days = [(end - timedelta(days=len(amounts) - 1 - i)).isoformat() for i in range(len(amounts))]
        db.upsert_rows(self.conn, "bars_daily", [
            {"code": code, "trade_date": day, "close": close, "amount": amount}
            for day, amount in zip(days, amounts)
        ], ["code", "trade_date"])
        db.upsert_rows(self.conn, "etf_shares", [
            {"code": code, "trade_date": days[-2], "shares": shares_prev, "nav": nav},
            {"code": code, "trade_date": days[-1], "shares": shares_today, "nav": nav},
        ], ["code", "trade_date"])

    def test_share_inflow_plus_volume_spike_is_a_hit(self):
        self._seed()
        result = support.scan(self.conn, self.cfg, "2026-09-18")
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["hits"]), 1)
        self.assertEqual(result["level"], "P2")          # 只有一只 → P2
        item = result["hits"][0]
        self.assertAlmostEqual(item["shares_pct"], 2.0, places=2)
        self.assertGreaterEqual(item["amount_ratio"], 2.0)
        self.assertGreater(item["inflow"], 200_000_000)

    def test_volume_alone_is_not_enough(self):
        """放量但份额没动 → 只是二级市场换手，不算托底。"""
        self._seed(shares_today=10_000_000_000.0)
        result = support.scan(self.conn, self.cfg, "2026-09-18")
        self.assertEqual(result["hits"], [])
        self.assertIsNone(result["level"])

    def test_inflow_alone_is_not_enough(self):
        """份额涨了但没放量 → 可能只是常规申赎，也不记。"""
        self._seed(amounts=[1e8] * 20 + [1.05e8])
        result = support.scan(self.conn, self.cfg, "2026-09-18")
        self.assertEqual(result["hits"], [])

    def test_small_inflow_is_filtered_out(self):
        """小 ETF 的份额波动太容易触发，用金额门槛挡掉。"""
        self._seed(shares_prev=1_000_000.0, shares_today=1_020_000.0)
        result = support.scan(self.conn, self.cfg, "2026-09-18")
        self.assertEqual(result["hits"], [])

    def test_missing_today_share_is_skipped_not_guessed(self):
        self._seed()
        db.query_one(self.conn, "SELECT 1")   # 让连接活着
        result = support.scan(self.conn, self.cfg, "2026-09-19")
        self.assertTrue(result["ok"])
        self.assertEqual(result["items"], [])            # 没有 19 号的份额 → 不猜

    def test_multi_count_upgrades_to_p1(self):
        """多只宽基齐步走才是强证据——这也是"国家队"最典型的样子。"""
        self.cfg["support"]["etfs"] = ["SH510300", "SH510050", "SH512100"]
        for code in self.cfg["support"]["etfs"]:
            self._seed(code=code, shares_prev=1e10, shares_today=1.05e10,
                       amounts=[1e8] * 20 + [6e8])
        result = support.scan(self.conn, self.cfg, "2026-09-18")
        self.assertEqual(len(result["hits"]), 3)
        self.assertEqual(result["level"], "P1")

    def test_record_writes_aggregate_and_one_alert(self):
        self._seed()
        result = support.scan(self.conn, self.cfg, "2026-09-18")
        saved = support.record(self.conn, self.cfg, result)
        self.assertEqual(saved, 1)
        row = db.query_one(self.conn, "SELECT * FROM support_days WHERE trade_date='2026-09-18'")
        self.assertEqual(row["level"], "P2")
        self.assertEqual(row["etf_count"], 1)
        self.assertGreater(row["net_inflow"], 0)
        alert = db.query_one(
            self.conn, "SELECT * FROM alerts WHERE signal_type='SUPPORT_INFLOW'")
        self.assertEqual(alert["code"], CODE)
        self.assertIn("疑似托底", alert["message"])
        self.assertEqual(len(support.history(self.conn, 10)), 1)

    def test_record_is_silent_on_a_normal_day(self):
        self._seed(shares_today=10_000_000_000.0)
        result = support.scan(self.conn, self.cfg, "2026-09-18")
        self.assertEqual(support.record(self.conn, self.cfg, result), 0)
        self.assertEqual(db.query_one(self.conn, "SELECT COUNT(*) AS n FROM support_days")["n"], 0)
        self.assertEqual(db.query_one(self.conn, "SELECT COUNT(*) AS n FROM alerts")["n"], 0)

    def test_empty_database_says_what_to_run(self):
        result = support.scan(self.conn, self.cfg)
        self.assertFalse(result["ok"])
        self.assertIn("每日任务", result["message"])


if __name__ == "__main__":
    unittest.main()
