"""页面用的两个新接口：/api/freshness（数据有多新）与 /api/quotes（实时报价）。

它们都是"给页面看的"，所以要求的性质是：**永远别抛异常**——断网、没有数据源、
空库都要给出一句人话，页面才不至于白屏。
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from collector import db, server, tasks
from collector.config import load_config, use_fixture_sources

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FreshnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=root)
        self.cfg["_db_path"] = str(root / "t.db")
        self.conn = db.connect(self.cfg["_db_path"])
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")
        self.app = server.App(self.cfg)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_empty_database_still_reports(self):
        payload = self.app.freshness()
        for key in ("daily", "features", "breadth", "intraday", "screen", "session", "trading"):
            self.assertIn(key, payload)
        self.assertIsNone(payload["daily"])
        # trading 取决于是不是交易时段，测试不该依赖运行时刻；这里只要它是布尔值
        self.assertIsInstance(payload["trading"], bool)
        self.assertIn(payload["session"], ("交易中", "午休", "已收盘", "未开盘", "非交易日"))

    def test_dates_come_from_the_tables(self):
        db.upsert_rows(self.conn, "bars_daily", [{
            "code": "SH600000", "trade_date": "2026-09-16", "close": 10.0,
            "volume": 100.0, "source": "test",
        }], ["code", "trade_date"])
        payload = self.app.freshness()
        self.assertEqual(payload["daily"], "2026-09-16")


class QuotesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=root)
        use_fixture_sources(self.cfg)                 # 离线：夹具源也给报价
        self.cfg["_db_path"] = str(root / "t.db")
        conn = db.connect(self.cfg["_db_path"])
        db.init_db(conn, PROJECT_ROOT / "schema.sql")
        conn.close()
        self.app = server.App(self.cfg)

    def tearDown(self):
        self.tmp.cleanup()

    def test_quotes_carry_the_fields_the_page_needs(self):
        payload = self.app.quotes(["SH000300"])
        self.assertIsNone(payload["error"])
        self.assertTrue(payload["items"])
        quote = payload["items"][0]
        for key in ("code", "close", "dt"):
            self.assertIn(key, quote)
        self.assertIn("session", payload)

    def test_repeated_calls_are_cached(self):
        first = self.app.quotes(["SH000300"])
        second = self.app.quotes(["SH000300"])
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])              # 20 秒内不重复问免费源

    def test_missing_source_reports_instead_of_crashing(self):
        with mock.patch.object(tasks, "quote_source", return_value=None):
            payload = self.app.quotes(["SH000300"])
        self.assertEqual(payload["items"], [])
        self.assertIn("数据源", payload["error"])

    def test_source_failure_is_swallowed(self):
        with mock.patch.object(tasks, "quote_source", side_effect=RuntimeError("boom")):
            payload = self.app.quotes(["SH000300"])
        self.assertEqual(payload["items"], [])


class RouteTests(unittest.TestCase):
    def test_new_routes_are_advertised(self):
        cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=PROJECT_ROOT)
        cfg["_db_path"] = str(PROJECT_ROOT / "data" / "selftest.db")
        status, _, body = server.dispatch(server.App(cfg), "GET", "/api/health")
        self.assertEqual(status, 200)
        routes = str(body)
        self.assertIn("/api/freshness", routes)
        self.assertIn("/api/quotes", routes)


if __name__ == "__main__":
    unittest.main()
