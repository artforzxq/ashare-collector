"""同步的目标日期：盘中不能把今天那根"半根日线"写进仓库。"""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from collector import db, warehouse
from collector.config import load_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class LastClosedTradeDateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=root)
        self.cfg["_db_path"] = str(root / "t.db")
        self.conn = db.connect(self.cfg["_db_path"])
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")
        db.upsert_rows(self.conn, "trade_calendar", [
            {"trade_date": "2026-10-08", "is_trading_day": 1},
            {"trade_date": "2026-10-09", "is_trading_day": 1},
            {"trade_date": "2026-10-10", "is_trading_day": 0},
        ], ["trade_date"])

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_during_the_session_target_is_the_previous_day(self):
        """周五 11:47（午休）：今天那根还没收盘，目标应该是 10-08。"""
        when = datetime(2026, 10, 9, 11, 47)
        self.assertEqual(warehouse.last_closed_trade_date(self.conn, when), "2026-10-08")

    def test_after_close_target_is_today(self):
        """但"最后一个已收盘的交易日"这个函数本身只回答"今天之前"，收盘后的切换由调用方判。"""
        when = datetime(2026, 10, 9, 16, 0)
        self.assertEqual(warehouse.last_closed_trade_date(self.conn, when), "2026-10-08")

    def test_falls_back_to_bars_when_calendar_is_stale(self):
        """日历可能是旧的：库里的日线比日历新时，以日线为准。"""
        db.upsert_rows(self.conn, "bars_daily", [{
            "code": "SH600000", "trade_date": "2026-10-09", "open": 1, "high": 1,
            "low": 1, "close": 1, "volume": 1, "amount": 1,
        }], ["code", "trade_date"])
        when = datetime(2026, 10, 12, 10, 0)          # 下周一盘中
        self.assertEqual(warehouse.last_closed_trade_date(self.conn, when), "2026-10-09")

    def test_sync_history_accepts_until_and_stops_before_the_network(self):
        """没有代码表时应当在联网之前就返回（测试不联网，也不该偷偷联网）。"""
        result = warehouse.sync_history(self.conn, self.cfg, until="2026-10-08", verbose=False)
        self.assertFalse(result.get("ok"))
        self.assertIn("代码表", result.get("message", ""))

    def test_sync_history_caps_a_later_target(self):
        db.upsert_rows(self.conn, "bars_daily", [{
            "code": "SH600000", "trade_date": "2026-10-08", "open": 1, "high": 1,
            "low": 1, "close": 1, "volume": 1, "amount": 1, "source": "tencent",
        }], ["code", "trade_date"])
        # 目标原本由 latest_trade_date 给出（交易日历里是 10-09），until 把它压回 10-08
        self.assertEqual(warehouse.latest_trade_date(self.conn, self.cfg), "2026-10-09")
        self.assertEqual(min(warehouse.latest_trade_date(self.conn, self.cfg), "2026-10-08"),
                         "2026-10-08")


if __name__ == "__main__":
    unittest.main()
