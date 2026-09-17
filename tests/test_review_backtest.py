"""复盘口径与参数回测的离线测试（合成数据，不联网）。"""

import tempfile
import unittest
from pathlib import Path

from collector import backtest, db, review
from collector.config import load_config
from tests.test_server import CONFIG_SAMPLE, _seed_db


def _series(count: int = 30, start: float = 100.0, step: float = 1.0):
    """构造一段每天涨 step 的行情，方便手算前瞻收益。"""
    bars, dates = [], []
    for index in range(count):
        day = f"2026-01-{index + 1:02d}"
        close = start + step * index
        bars.append({"trade_date": day, "open": close - 0.5, "high": close + 1,
                     "low": close - 1, "close": close, "volume": 1000 + index})
        dates.append(day)
    return bars, dates


class ForwardReturnTests(unittest.TestCase):
    def test_entry_is_next_day_and_horizon_counts_trading_days(self):
        bars, dates = _series(10)                      # close = 100,101,...,109
        # 信号在 1 日 → 次日(2 日, close=101)建仓 → 第 5 个交易日 = 7 日(close=106)
        value = review.forward_return(bars, dates, "2026-01-01", 5)
        self.assertAlmostEqual(value, (106 / 101 - 1) * 100, places=4)

    def test_one_day_horizon(self):
        bars, dates = _series(5)
        value = review.forward_return(bars, dates, "2026-01-01", 1)
        self.assertAlmostEqual(value, (102 / 101 - 1) * 100, places=4)

    def test_not_enough_data_returns_none(self):
        bars, dates = _series(5)
        self.assertIsNone(review.forward_return(bars, dates, "2026-01-04", 5))
        self.assertIsNone(review.forward_return(bars, dates, "2026-01-05", 1))


class BackfillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cfg_path = root / "config.yaml"
        cfg_path.write_text(CONFIG_SAMPLE, encoding="utf-8")
        cls.cfg = load_config(cfg_path, project_root=root)
        cls.cfg["_db_path"] = str(root / "market.db")
        cls.conn = db.connect(cls.cfg["_db_path"])
        db.init_db(cls.conn, Path(__file__).resolve().parents[1] / "schema.sql")
        bars, _ = _series(30)
        db.upsert_rows(cls.conn, "bars_daily",
                       [{"code": "SH000300", **bar} for bar in bars], ["code", "trade_date"])
        db.upsert_rows(cls.conn, "alerts", [{
            "created_at": "2026-01-01 15:00:00", "trade_date": "2026-01-01",
            "code": "SH000300", "level": "P1", "signal_type": "STATE_TO_UP",
            "message": "测试信号",
        }], ["code", "trade_date", "signal_type", "level"])

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def test_backfill_and_stats(self):
        info = review.backfill_outcomes(self.conn)
        self.assertEqual(info["alerts"], 1)
        row = db.query_one(self.conn, "SELECT outcome_5d, outcome_20d FROM alerts") 
        self.assertAlmostEqual(row["outcome_5d"], (106 / 101 - 1) * 100, places=3)
        self.assertAlmostEqual(row["outcome_20d"], (121 / 101 - 1) * 100, places=3)
        stats = review.signal_stats(self.conn)
        self.assertEqual(stats[0]["n"], 1)
        self.assertEqual(stats[0]["win5"], 1)
        self.assertIn("信号复盘", review.report(self.conn))

    def test_backfill_is_idempotent(self):
        review.backfill_outcomes(self.conn)
        again = review.backfill_outcomes(self.conn)
        self.assertEqual(again["alerts"], 0)          # 值没变就不再写


class BacktestTests(unittest.TestCase):
    def _fixture(self):
        bars, _ = _series(30)
        rows = []
        for index, bar in enumerate(bars):
            rows.append({
                **bar, "trend_score": 80.0 if index >= 5 else 50.0, "state": "range",
                "raw_values": {}, "range_position": 0.5,
            })
        return {"SH000300": bars}, {"SH000300": rows}

    def test_make_params_is_symmetric(self):
        params = backtest.make_params(70, 2, 3)
        self.assertEqual(params["enter_up"], 70)
        self.assertEqual(params["exit_up"], 55)
        self.assertEqual(params["enter_down"], 30)
        self.assertEqual(params["exit_down"], 45)
        self.assertEqual(params["confirm_days"], 2)

    def test_evaluate_counts_signals_and_benchmarks(self):
        bars, base = self._fixture()
        fwd = backtest.forward_arrays(bars)
        result = backtest.evaluate(bars, base, fwd, backtest.make_params(70, 1, 1))
        self.assertGreaterEqual(result["signals"], 1)
        self.assertGreater(result["n5"], 0)
        self.assertIsNotNone(result["excess5"])
        # 单边上涨的合成数据里，信号和基准都该是正的
        self.assertGreater(result["avg5"], 0)
        self.assertGreater(result["base5"], 0)

    def test_run_grid_marks_current_params(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg_path = root / "config.yaml"
            cfg_path.write_text(CONFIG_SAMPLE, encoding="utf-8")
            # 用项目真实配置（里面有 factors 台账），只把库指到临时目录；
            # 不带 factors 的配置算不出趋势分，回测自然一个信号都没有。
            cfg = load_config(Path(__file__).resolve().parents[1] / "config.yaml", project_root=root)
            cfg["_db_path"] = str(root / "market.db")
            cfg["_config_path"] = str(cfg_path)
            cfg["watchlist"] = {"indices": ["SH000300"], "etfs": [], "stocks": []}
            conn = db.connect(cfg["_db_path"])
            db.init_db(conn, Path(__file__).resolve().parents[1] / "schema.sql")
            # 陡一点：趋势分要能站上 70 才有信号，平缓的合成数据本来就该没信号
            bars, _ = _series(200, step=3.0)
            db.upsert_rows(conn, "bars_daily",
                           [{"code": "SH000300", **bar} for bar in bars], ["code", "trade_date"])
            # 观察池里有这只票（配置里 indices 已含 SH000300）
            result = backtest.run_grid(conn, cfg, grid={"enter_up": (70, 75),
                                                        "confirm_days": (2,),
                                                        "min_state_days": (3,)}, verbose=False)
            conn.close()
            self.assertTrue(result["ok"])
            self.assertEqual(len(result["results"]), 2)
            self.assertGreater(max(row["signals"] for row in result["results"]), 0)
            text = backtest.render_report(result)
            self.assertIn("←当前", text)
            self.assertIn("基准", text)


if __name__ == "__main__":
    unittest.main()
