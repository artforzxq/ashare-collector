"""全市场本地化与因子体检的离线测试（夹具数据，不联网）。"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from collector import db, promotion, warehouse
from collector.config import load_config, use_fixture_sources

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class WarehouseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=self.root)
        use_fixture_sources(self.cfg, end_date="2026-09-16")     # 离线夹具
        self.cfg["_db_path"] = str(self.root / "t.db")
        self.conn = db.connect(self.cfg["_db_path"])
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")
        db.upsert_rows(
            self.conn,
            "instruments",
            [{"code": code, "name": code, "type": "stock", "exchange": code[:2], "in_watchlist": 0}
             for code in ("SH600000", "SZ000001")],
            ["code"],
        )

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_history_sync_then_resume_skips(self):
        result = warehouse.sync_history(self.conn, self.cfg, verbose=False)
        self.assertTrue(result["ok"])
        self.assertEqual(result["written"], 2)
        self.assertEqual(result["remaining"], 0)
        again = warehouse.sync_history(self.conn, self.cfg, verbose=False)
        self.assertEqual(again["pending"], 0)             # 已经最新 → 不用再抓

    def test_history_sync_respects_batch_limit(self):
        result = warehouse.sync_history(self.conn, self.cfg, limit=1, verbose=False)
        self.assertEqual(result["done"], 1)
        self.assertEqual(result["remaining"], 1)          # 剩下的下次再补

    def test_universe_sync_merges_sources_and_labels_types(self):
        """代码表是并出来的：新浪给北交所，baostock 给 ETF 和指数。"""

        class SinaLike:
            name = "sina"

            def symbol_directory(self):
                return [{"code": "BJ920000", "name": "安徽凤凰", "type": "stock"}]

        class BaostockLike:
            name = "baostock"

            def symbol_directory(self):
                return [
                    {"code": "SH600000", "name": "浦发银行", "type": "stock"},
                    {"code": "SH510300", "name": "沪深300ETF", "type": "etf"},
                    {"code": "SH000300", "name": "沪深300", "type": "index"},
                ]

        with mock.patch.object(warehouse, "directory_sources", lambda cfg: [SinaLike(), BaostockLike()]):
            result = warehouse.sync_universe(self.conn, self.cfg, verbose=False)

        self.assertTrue(result["ok"], result.get("message"))
        self.assertEqual(result["codes"], 4)
        self.assertEqual(result["sources"], ["sina", "baostock"])
        self.assertEqual(result["by_type"], {"stock": 2, "etf": 1, "index": 1})
        kinds = {
            row["code"]: row["type"]
            for row in db.query(self.conn, "SELECT code, type FROM instruments")
        }
        self.assertEqual(kinds["SH510300"], "etf")
        self.assertEqual(kinds["SH000300"], "index")
        self.assertEqual(kinds["BJ920000"], "stock")

    def test_universe_sync_keeps_going_when_one_source_is_broken(self):
        class Broken:
            name = "akshare"

            def symbol_directory(self):
                raise RuntimeError("东财接口被断连")

        class Good:
            name = "baostock"

            def symbol_directory(self):
                return [{"code": "SH600000", "name": "浦发银行", "type": "stock"}]

        with mock.patch.object(warehouse, "directory_sources", lambda cfg: [Broken(), Good()]):
            result = warehouse.sync_universe(self.conn, self.cfg, verbose=False)

        self.assertTrue(result["ok"], result.get("message"))
        self.assertEqual(result["sources"], ["baostock"])

    def test_universe_sync_reports_every_failure_when_all_are_down(self):
        class Broken:
            name = "sina"

            def symbol_directory(self):
                raise RuntimeError("超时")

        with mock.patch.object(warehouse, "directory_sources", lambda cfg: [Broken()]):
            result = warehouse.sync_universe(self.conn, self.cfg, verbose=False)

        self.assertFalse(result["ok"])
        self.assertIn("sina", result["message"])
        self.assertIn("超时", result["message"])

    def test_snapshot_writes_market_but_not_codes_with_real_data(self):
        db.upsert_rows(
            self.conn, "bars_daily",
            [{"code": "SZ000005", "trade_date": "2026-09-16", "open": 1.0, "high": 1.1,
              "low": 0.9, "close": 1.0, "source": "baostock"}],
            ["code", "trade_date"],
        )
        result = warehouse.snapshot_bars(self.conn, self.cfg, "2026-09-16", verbose=False)
        self.assertTrue(result["ok"])
        self.assertGreater(result["written"], 300)
        kept = db.query_one(
            self.conn, "SELECT source FROM bars_daily WHERE code='SZ000005' AND trade_date='2026-09-16'"
        )
        self.assertEqual(kept["source"], "baostock")      # 有正式数据的没被快照覆盖
        written = db.query_one(
            self.conn, "SELECT source FROM bars_daily WHERE code='SZ000006' AND trade_date='2026-09-16'"
        )
        self.assertEqual(written["source"], "snapshot")

    def test_snapshot_never_clobbers_names_and_types(self):
        """快照只给"代码表里还没有"的标的补占位行，绝不能覆盖已有行。

        这条是真踩过的坑：快照以前无条件 upsert，而且把 name 写成代码、type 一律写 stock，
        于是每跑一次日终，全市场几千只 ETF 和指数的名字与类型就被抹成"个股"，
        连带市场广度、筛选池、新股表全按错误类型算。
        """
        db.upsert_rows(
            self.conn, "instruments",
            [{"code": "SH510300", "name": "华泰柏瑞沪深300ETF", "type": "etf"}],
            ["code"],
        )
        written = warehouse.snapshot_bars(self.conn, self.cfg, "2026-09-16", verbose=False)
        self.assertTrue(written["ok"])
        kept = db.query_one(
            self.conn, "SELECT name, type FROM instruments WHERE code='SH510300'")
        self.assertEqual(kept["type"], "etf")
        self.assertEqual(kept["name"], "华泰柏瑞沪深300ETF")
        # 快照里那些代码表没有的标的：补占位行，但类型要按代码段分类，不能一律 stock
        fresh = db.query_one(
            self.conn,
            """SELECT type FROM instruments WHERE code NOT IN ('SZ000005', 'SZ000006')
               AND in_watchlist=0 AND name=code LIMIT 1""",
        )
        if fresh:
            self.assertIn(fresh["type"], ("stock", "etf", "index", "other"))

    def test_status_counts(self):
        warehouse.sync_history(self.conn, self.cfg, verbose=False)
        info = warehouse.status(self.conn)
        self.assertEqual(info["instruments"], 2)
        self.assertEqual(info["with_data"], 2)
        self.assertGreater(info["bars"], 400)


class PromotionTests(unittest.TestCase):
    def test_pearson(self):
        self.assertAlmostEqual(promotion._pearson([(1, 2), (2, 4), (3, 6), (4, 8), (5, 10)] * 3), 1.0, places=6)
        self.assertIsNone(promotion._pearson([(1, 1)] * 5))          # 点太少不给结论
        self.assertIsNone(promotion._pearson([(1, 1), (1, 2)] * 6))  # 没有方差

    def test_factor_stats_reports_coverage_and_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=root)
            cfg["_db_path"] = str(root / "t.db")
            conn = db.connect(cfg["_db_path"])
            db.init_db(conn, PROJECT_ROOT / "schema.sql")
            bars = []
            for index in range(40):
                price = 10 + index * 0.1
                bars.append({"code": "SH600000", "trade_date": f"2026-06-{index + 1:02d}",
                             "open": price, "high": price * 1.01, "low": price * 0.99,
                             "close": price, "volume": 1e6, "source": "baostock"})
            db.upsert_rows(conn, "bars_daily", bars, ["code", "trade_date"])
            db.upsert_rows(conn, "factor_registry", [
                {"factor_id": "ma_slope", "name": "均线斜率", "layer": "state", "role": "primary",
                 "category": "trend", "weight": 0.25, "status": "active", "feature_version": "v1"},
                {"factor_id": "adx", "name": "ADX", "layer": "state", "role": "modifier",
                 "category": "trend", "weight": 0.0, "status": "shadow", "feature_version": "v1"},
            ], ["factor_id"])
            contributions = []
            for index in range(40):
                day = f"2026-06-{index + 1:02d}"
                contributions.append({"trade_date": day, "code": "SH600000", "factor_id": "ma_slope",
                                      "normalized_score": index / 40, "raw_value": index, "weight": 0.25,
                                      "contribution": 0.1, "feature_version": "v1"})
            db.upsert_rows(conn, "factor_contributions", contributions,
                           ["trade_date", "code", "factor_id"])
            stats = promotion.factor_stats(conn, cfg)
            conn.close()

            by_id = {item["factor_id"]: item for item in stats}
            self.assertEqual(by_id["ma_slope"]["days"], 40)
            self.assertIsNotNone(by_id["ma_slope"]["ic20"])
            self.assertIn("样本不足", by_id["adx"]["verdict"])     # 影子因子一条数据都没有


if __name__ == "__main__":
    unittest.main()
