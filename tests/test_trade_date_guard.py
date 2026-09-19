"""别把"今天"当交易日：接口失败时的兜底，以及非交易日的两道闸门。

真实事故（2026-09-19，周六）：那天腾讯接口抽风，`resolve_trade_date` 静默退回
"今天"，于是日终把周六当交易日跑了一遍，还把周五的快照数据贴上周六的日期写进了
bars_daily——7722 根假 K 线，四价与周五完全相同，肉眼看不出来，均线和形态却全错了。

所以这里测三件事：
  1. 兜底**不能是今天**：源拿不到日历时退回本地最新交易日 / 最近工作日；
  2. 日终有前置闸门：那天不是交易日就不跑，一行数据都不写；
  3. 快照有写入闸门：这是最后一道防线（快照接口在周末照样返回上一交易日的数据）。
"""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from collector import db, tasks, warehouse
from collector.config import load_config, use_fixture_sources
from collector.sources.base import DataSourceError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SATURDAY = "2026-09-19"          # 2026-09-19 是周六
FRIDAY = "2026-09-18"


class _DeadSource:
    """接口挂掉时的样子：日历要什么都抛 DataSourceError（当天就是这个情况）。"""

    name = "dead"

    def trade_calendar(self, start, end):
        raise DataSourceError("接口返回了空响应（Expecting value: line 1 column 1）")


class ResolveTradeDateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=Path(self.tmp.name))
        self.cfg["_db_path"] = str(Path(self.tmp.name) / "t.db")
        self.conn = db.connect(self.cfg["_db_path"])
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_dead_source_falls_back_to_the_local_trading_day(self):
        db.upsert_rows(self.conn, "trade_calendar",
                       [{"trade_date": FRIDAY, "is_trading_day": 1}], ["trade_date"])
        self.conn.commit()
        self.assertEqual(tasks.resolve_trade_date(_DeadSource(), self.cfg, None, conn=self.conn), FRIDAY)

    def test_dead_source_without_local_data_still_avoids_the_weekend(self):
        """连本地日历都没有时，至少不能落在周末——事故当天返回的就是周六。"""
        result = tasks.resolve_trade_date(_DeadSource(), self.cfg, None, conn=self.conn)
        self.assertLess(datetime.strptime(result, "%Y-%m-%d").weekday(), 5)
        self.assertLessEqual(result, datetime.now().strftime("%Y-%m-%d"))

    def test_explicit_date_still_wins(self):
        self.assertEqual(tasks.resolve_trade_date(_DeadSource(), self.cfg, FRIDAY, conn=self.conn), FRIDAY)

    def test_fixture_source_uses_its_own_last_day(self):
        use_fixture_sources(self.cfg, end_date="2026-09-16")
        from collector.sources import build_source

        source = build_source(self.cfg["sources"]["primary"], self.cfg)
        self.assertEqual(tasks.resolve_trade_date(source, self.cfg, None, conn=self.conn), "2026-09-16")


class DailyGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=Path(self.tmp.name))
        use_fixture_sources(self.cfg, end_date="2026-09-16")
        self.cfg["_db_path"] = str(Path(self.tmp.name) / "t.db")
        self.conn = db.connect(self.cfg["_db_path"])
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_daily_refuses_to_run_on_a_non_trading_day(self):
        summary = tasks.run_daily(self.conn, self.cfg, trade_date=SATURDAY, verbose=False)
        self.assertEqual(summary["trade_date"], SATURDAY)
        self.assertTrue(any("不是交易日" in item for item in summary["issues"]), summary["issues"])
        self.assertEqual(summary["alerts"], [])

    def test_refusal_leaves_the_database_untouched(self):
        tasks.run_daily(self.conn, self.cfg, trade_date=SATURDAY, verbose=False)
        for table in ("bars_daily", "features_daily", "market_breadth", "alerts"):
            count = db.query_one(self.conn, f"SELECT COUNT(*) AS n FROM {table}")["n"]
            self.assertEqual(count, 0, table)

    def test_refusal_is_recorded_in_data_health(self):
        """跑没跑、为什么没跑，得能在体检里查到，不然就是静默失败。"""
        tasks.run_daily(self.conn, self.cfg, trade_date=SATURDAY, verbose=False)
        row = db.query_one(
            self.conn,
            "SELECT status, error_msg FROM data_health WHERE task='daily' ORDER BY created_at DESC LIMIT 1",
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "failed")
        self.assertIn("不是交易日", row["error_msg"])


class SnapshotGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=Path(self.tmp.name))
        use_fixture_sources(self.cfg, end_date="2026-09-16")
        self.cfg["_db_path"] = str(Path(self.tmp.name) / "t.db")
        self.conn = db.connect(self.cfg["_db_path"])
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_snapshot_refuses_a_non_trading_day(self):
        result = warehouse.snapshot_bars(self.conn, self.cfg, SATURDAY, verbose=False)
        self.assertFalse(result["ok"])
        self.assertIn("不是交易日", result["message"])
        self.assertEqual(db.query_one(self.conn, "SELECT COUNT(*) AS n FROM bars_daily")["n"], 0)

    def test_snapshot_defaults_to_the_local_latest_trading_day(self):
        """不传日期时用本地认得的最新交易日，而不是"今天"。"""
        db.upsert_rows(self.conn, "trade_calendar",
                       [{"trade_date": FRIDAY, "is_trading_day": 1}], ["trade_date"])
        self.conn.commit()
        result = warehouse.snapshot_bars(self.conn, self.cfg, None, verbose=False)
        # 夹具源没有全市场快照能力，所以这里只关心"它没有拿今天去写库"
        self.assertNotIn(SATURDAY, str(result.get("message", "")))


if __name__ == "__main__":
    unittest.main()
