import tempfile
import unittest
from pathlib import Path

from collector import db, report as report_mod, tasks
from collector.config import load_config, use_fixture_sources

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = load_config(PROJECT_ROOT / "config.yaml")
        use_fixture_sources(cfg, end_date="2026-09-16")   # 单元测试不联网
        # 观察池钉死：config.yaml 是用户会改的活配置（加自选、删自选），
        # 测试不能跟着一起变，否则测试会因为"用户改了自选"而失败。
        cfg["watchlist"] = {
            "indices": ["SH000001", "SH000300", "SH000905", "SH000852", "SZ399006"],
            "etfs": ["SH510300", "SH510050", "SZ159919", "SH510500", "SH588000"],
            "stocks": [],
        }
        cfg["_db_path"] = str(Path(cls.tmp.name) / "test.db")
        conn = db.connect(cfg["_db_path"])
        db.init_db(conn, PROJECT_ROOT / "schema.sql")
        cls.cfg = cfg
        cls.conn = conn
        cls.summary = tasks.run_daily(conn, cfg, verbose=False)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def test_cross_source_conflict_is_detected(self):
        info = self.summary["codes"]["SH000300"]
        self.assertEqual(info["conflicts"], 1)

    def test_helper_columns_are_actually_persisted(self):
        """辅助列要真的落库：它们曾经只活在内存里——特征算得对，库里却是 NULL，
        页面和体检读到的就是空。这类"算了但没存"的 bug 只能靠端到端跑一遍兜住。"""
        row = db.query_one(
            self.conn,
            """SELECT avg_amount_20d, turnover_ratio, swing_state, swing_low_1,
                      bars_since_swing_low
               FROM features_daily WHERE code='SH000300' AND swing_low_1 IS NOT NULL
               ORDER BY trade_date DESC LIMIT 1""",
        )
        self.assertIsNotNone(row, "摆动结构没有落库")
        self.assertIsNotNone(row["avg_amount_20d"])
        self.assertIsNotNone(row["turnover_ratio"])
        self.assertIn(row["swing_state"], (-1.0, 0.0, 1.0))

    def test_state_machine_reaches_uptrend(self):
        rows = db.query(
            self.conn,
            "SELECT state, trend_score FROM features_daily WHERE code='SH000300' ORDER BY trade_date DESC LIMIT 1",
        )
        self.assertEqual(rows[0]["state"], "up")
        self.assertGreater(rows[0]["trend_score"], 55)

    def test_levels_and_alerts_are_persisted(self):
        bands = db.query(self.conn, "SELECT COUNT(*) AS n FROM levels")
        alerts = db.query(self.conn, "SELECT COUNT(*) AS n FROM alerts")
        self.assertGreater(bands[0]["n"], 0)
        self.assertGreater(alerts[0]["n"], 0)

    def test_factor_contributions_are_auditable(self):
        rows = db.query(
            self.conn,
            "SELECT COUNT(*) AS n FROM factor_contributions WHERE code='SH000300'",
        )
        self.assertGreater(rows[0]["n"], 0)
        payload = report_mod.factor_report(self.conn, self.cfg, self.summary["trade_date"], "SH000300")
        self.assertIn("因子贡献", payload)

    def test_report_renders(self):
        text = report_mod.daily_report(self.conn, self.cfg, self.summary["trade_date"])
        self.assertIn("交易简报", text)
        self.assertIn("SH000300", text)


if __name__ == "__main__":
    unittest.main()
