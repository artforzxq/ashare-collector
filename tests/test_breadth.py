"""市场广度：用本地 K 线数涨跌家数。口径要和官方一致（只数个股、跳过停牌）。"""

import tempfile
import unittest
from pathlib import Path

from collector import breadth, db
from collector.config import load_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _bar(code, pct, close=10.0, pre_close=None, amount=1000.0, date="2026-01-05"):
    return {"code": code, "trade_date": date, "close": close, "pre_close": pre_close,
            "pct_chg": pct, "amount": amount}


class SummarizeTests(unittest.TestCase):
    def test_counts_up_down_flat(self):
        rows = [_bar("SH600000", 1.0), _bar("SZ000001", -2.0), _bar("SZ300001", 0.0)]
        record = breadth.summarize(rows, "2026-01-05", "test")
        self.assertEqual((record["up_count"], record["down_count"], record["flat_count"]), (1, 1, 1))
        self.assertEqual(record["coverage"], 3)

    def test_missing_pct_is_recovered_from_close_and_pre_close(self):
        rows = [_bar("SH600000", None, close=11.0, pre_close=10.0)]      # +10%
        record = breadth.summarize(rows, "2026-01-05", "test")
        self.assertEqual(record["up_count"], 1)
        self.assertEqual(record["limit_up_count"], 1)                    # 粗略口径

    def test_precise_limits_follow_the_board(self):
        main = [_bar("SH600000", 10.0, close=11.0, pre_close=10.0)]      # 主板：+10% 是涨停
        gem = [_bar("SZ300001", 10.0, close=11.0, pre_close=10.0)]       # 创业板：不是
        self.assertEqual(breadth.summarize(main, "d", "t", precise_limits=True)["limit_up_count"], 1)
        self.assertEqual(breadth.summarize(gem, "d", "t", precise_limits=True)["limit_up_count"], 0)

    def test_amounts_split_by_exchange_even_without_a_prefix(self):
        rows = [_bar("600000", 0.5, amount=100.0), _bar("000001", 0.5, amount=50.0)]
        record = breadth.summarize(rows, "d", "t")
        self.assertEqual(record["sh_amount"], 100.0)
        self.assertEqual(record["sz_amount"], 50.0)

    def test_empty_sample_returns_none(self):
        self.assertIsNone(breadth.summarize([], "d", "t"))


class LocalBreadthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=root)
        self.cfg["_db_path"] = str(root / "t.db")
        self.conn = db.connect(self.cfg["_db_path"])
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _seed(self):
        rows = [
            {"code": f"SH60000{i}", "trade_date": "2026-01-05", "close": 10.0 + i,
             "pre_close": 10.0, "pct_chg": 1.0 if i % 2 == 0 else -1.0, "amount": 1000.0}
            for i in range(5)
        ]
        # 指数和 ETF 不是"家"，不该进涨跌家数
        rows.append({"code": "SH000300", "trade_date": "2026-01-05", "close": 4000.0,
                     "pre_close": 3900.0, "pct_chg": 2.5, "amount": 0.0})
        rows.append({"code": "SH510300", "trade_date": "2026-01-05", "close": 4.0,
                     "pre_close": 3.9, "pct_chg": 2.5, "amount": 0.0})
        db.upsert_rows(self.conn, "bars_daily", rows, ["code", "trade_date"])
        db.upsert_rows(self.conn, "instruments", [
            *[{"code": f"SH60000{i}", "name": f"票{i}", "type": "stock"} for i in range(5)],
            {"code": "SH000300", "name": "沪深300", "type": "index"},
            {"code": "SH510300", "name": "沪深300ETF", "type": "etf"},
        ], ["code"])

    def test_only_stocks_are_counted(self):
        self._seed()
        record = breadth.from_local(self.conn, "2026-01-05")
        self.assertEqual(record["coverage"], 5)          # 5 只个股，不含指数/ETF
        self.assertEqual(record["up_count"], 3)
        self.assertEqual(record["down_count"], 2)

    def test_thin_sample_is_not_written(self):
        self._seed()
        info = breadth.backfill(self.conn, min_coverage=1000)
        self.assertEqual(info["written"], 0)
        self.assertEqual(info["thin"], 1)
        self.assertIsNone(db.query_one(self.conn, "SELECT 1 FROM market_breadth"))

    def test_sample_is_written_when_threshold_is_lowered(self):
        self._seed()
        info = breadth.backfill(self.conn, min_coverage=3)
        self.assertEqual(info["written"], 1)
        row = db.query_one(self.conn, "SELECT coverage, up_count, source FROM market_breadth")
        self.assertEqual(row["coverage"], 5)
        self.assertEqual(row["source"], "local")

    def test_snapshot_rows_are_kept_as_is(self):
        self._seed()
        db.upsert_rows(self.conn, "market_breadth",
                       [{"trade_date": "2026-01-05", "coverage": 5400, "up_count": 3000,
                         "down_count": 2000, "source": "akshare"}], ["trade_date"])
        info = breadth.backfill(self.conn, min_coverage=3)
        self.assertEqual(info["skipped"], 1)
        row = db.query_one(self.conn, "SELECT source FROM market_breadth")
        self.assertEqual(row["source"], "akshare")       # 快照覆盖更全，不覆盖它


if __name__ == "__main__":
    unittest.main()
