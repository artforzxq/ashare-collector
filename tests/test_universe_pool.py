"""标的池口径：哪些东西有资格进筛选和回测。

最容易错的一条是"指数算不算一只标的"：指数没有成交额可比，拿它过流动性门槛
等于永远免检，于是它在抽样里会越占越多，把基准和信号一起带偏。
默认不进池子，配置和 `--include-index` 都能打开。
"""

import argparse
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from collector import cli, config, db, universe
from collector.config import load_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _days(count: int) -> list[str]:
    start = datetime(2026, 1, 5)
    return [(start + timedelta(days=index)).strftime("%Y-%m-%d") for index in range(count)]


class UniversePoolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=self.root)
        self.cfg["universe"] = {"min_avg_amount_60d": 30_000_000, "include_index": False}
        self.cfg["_db_path"] = str(self.root / "t.db")
        self.conn = db.connect(self.cfg["_db_path"])
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")
        self._seed()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _seed(self):
        """一只大票、一只小票、一个指数，各 70 根日线。"""
        db.upsert_rows(
            self.conn,
            "instruments",
            [
                {"code": "SH600000", "name": "浦发银行", "type": "stock"},
                {"code": "SH600001", "name": "小票", "type": "stock"},
                {"code": "SH000300", "name": "沪深300", "type": "index"},
            ],
            ["code"],
        )
        amounts = {"SH600000": 120_000_000.0, "SH600001": 2_000_000.0, "SH000300": None}
        db.upsert_rows(
            self.conn,
            "bars_daily",
            [
                {"code": code, "trade_date": day, "close": 10.0, "amount": amount, "quality_flag": "ok"}
                for code, amount in amounts.items()
                for day in _days(70)
            ],
            ["code", "trade_date"],
        )

    def test_index_is_out_of_the_pool_by_default(self):
        picked = universe.select_codes(self.conn, self.cfg, min_bars=60)
        self.assertEqual(picked["codes"], ["SH600000"])
        self.assertEqual(picked["dropped_index"], 1)
        self.assertEqual(picked["dropped_liquidity"], 1)      # 小票被成交额门槛挡住

    def test_index_can_be_switched_back_on(self):
        self.cfg["universe"]["include_index"] = True
        picked = universe.select_codes(self.conn, self.cfg, min_bars=60)
        self.assertIn("SH000300", picked["codes"])
        self.assertEqual(picked["dropped_index"], 0)

    def test_index_without_a_type_row_is_still_an_index(self):
        """历史数据里没有 instruments 行的老代码，靠号段也要认出来。"""
        db.upsert_rows(
            self.conn,
            "bars_daily",
            [{"code": "SZ399006", "trade_date": day, "close": 3000.0, "amount": None,
              "quality_flag": "ok"} for day in _days(70)],
            ["code", "trade_date"],
        )
        picked = universe.select_codes(self.conn, self.cfg, min_bars=60)
        self.assertNotIn("SZ399006", picked["codes"])
        self.assertEqual(picked["dropped_index"], 2)

    def test_defaults_say_index_is_excluded(self):
        self.assertFalse(config.DEFAULTS["universe"]["include_index"])
        self.assertFalse(universe.include_index({}))

    def test_cli_flag_turns_it_on_for_one_run(self):
        cfg = {"universe": {"include_index": False}}
        self.assertTrue(cli._apply_index_choice(cfg, argparse.Namespace(include_index=True))["universe"]["include_index"])
        cfg2 = {"universe": {"include_index": False}}
        self.assertFalse(cli._apply_index_choice(cfg2, argparse.Namespace(include_index=False))["universe"]["include_index"])


if __name__ == "__main__":
    unittest.main()
