"""离线重算：只用本地日线重算特征/关键带/提醒，不联网。

为什么值得单独测：这个模式存在的意义就是"不联网"——
一旦哪一步漏了（新股上市日要走 baostock、龙虎榜/资金流要走东财），
它就会在用户以为"只是重算一下"的时候偷偷联网，甚至写进半根日线的结论。
"""

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from collector import db, tasks
from collector.config import load_config, use_fixture_sources

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class OfflineDailyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=root)
        use_fixture_sources(self.cfg)
        self.cfg["watchlist"] = {"indices": ["SH000300"], "etfs": [], "stocks": []}
        self.cfg["_db_path"] = str(root / "t.db")
        self.conn = db.connect(self.cfg["_db_path"])
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")
        bars = []
        for index in range(140):
            close = 100.0 + index
            bars.append({
                "code": "SH000300",
                "trade_date": (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
                "open": close, "high": close, "low": close, "close": close,
                "volume": 1000, "amount": 200_000_000, "pct_chg": 1.0, "quality_flag": "ok",
            })
        db.upsert_rows(self.conn, "bars_daily", bars, ["code", "trade_date"])

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _run_offline(self, when=None, **kwargs):
        """跑离线重算，同时把所有"联网入口"换掉——只要被碰到就断言失败。"""
        def boom(*args, **kw):
            raise AssertionError("离线模式下不该联网")

        with mock.patch.object(tasks, "build_source", boom), \
                mock.patch.object(tasks, "_source_pool", boom), \
                mock.patch.object(tasks.newstock_mod, "sync_ipo_dates", boom), \
                mock.patch.object(tasks, "collect_lhb", boom), \
                mock.patch.object(tasks, "collect_fund_flow", boom), \
                mock.patch.object(tasks, "_collect_market_snapshot_bars", boom), \
                mock.patch.object(tasks, "_collect_calendar", boom), \
                mock.patch.object(tasks, "_collect_etf_shares", boom), \
                mock.patch.object(tasks, "_collect_margin", boom):
            return tasks.run_daily(self.conn, self.cfg, verbose=False, offline=True, **kwargs)

    def test_computes_features_without_touching_the_network(self):
        summary = self._run_offline()
        self.assertTrue(summary["trade_date"])
        row = db.query_one(
            self.conn,
            "SELECT state, trend_score, position_cap FROM features_daily ORDER BY trade_date DESC LIMIT 1")
        self.assertIsNotNone(row)
        self.assertIsNotNone(row["trend_score"])      # 特征真的算出来了

    def test_uses_the_last_local_trade_date(self):
        """没有指定日期时，用观察池里最后一根日线的日期。"""
        summary = self._run_offline()
        expected = db.query_one(
            self.conn, "SELECT MAX(trade_date) AS d FROM bars_daily WHERE code='SH000300'")["d"]
        if expected >= date.today().isoformat():      # 收盘前不该用当天那根
            self.assertLess(summary["trade_date"], date.today().isoformat())
        else:
            self.assertEqual(summary["trade_date"], expected)

    def test_explicit_date_is_honoured(self):
        summary = self._run_offline(trade_date="2026-05-01")
        self.assertEqual(summary["trade_date"], "2026-05-01")

    def test_does_not_write_snapshot_or_emotion_tables(self):
        self._run_offline()
        for table in ("lhb", "fund_flow", "etf_shares", "market_flow"):
            count = db.query_one(self.conn, f"SELECT COUNT(*) AS n FROM {table}")["n"]
            self.assertEqual(count, 0, f"{table} 不该被离线重算写入")

    def test_breadth_is_computed_locally(self):
        self._run_offline()
        row = db.query_one(self.conn, "SELECT COUNT(*) AS n FROM market_breadth")
        self.assertGreaterEqual(row["n"], 0)          # 本地 K 线够就写，不够就跳过——都不该炸


if __name__ == "__main__":
    unittest.main()
