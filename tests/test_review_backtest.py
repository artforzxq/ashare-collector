"""复盘口径与参数回测的离线测试（合成数据，不联网）。"""

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from collector import backtest, db, review, universe
from collector.config import load_config
from tests.test_server import CONFIG_SAMPLE, _seed_db


def _series(count: int = 30, start: float = 100.0, step: float = 1.0):
    """构造一段每天涨 step 的行情，方便手算前瞻收益。"""
    bars, dates = [], []
    for index in range(count):
        day = (date(2026, 1, 1) + timedelta(days=index)).isoformat()
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
            # 合成数据里只有这一个指数，要验的是"当前参数有没有被标出来"，
            # 所以这次把指数放回标的池（默认是剔除的，见 universe.include_index）。
            cfg["universe"]["include_index"] = True
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


def _flat_bars(count: int = 12, close: float = 10.0):
    """一条平直的行情：方便把涨跌停和成本的影响单独拎出来看。"""
    bars = []
    for index in range(count):
        day = (date(2026, 1, 1) + timedelta(days=index)).isoformat()
        bars.append({"trade_date": day, "open": close, "high": close, "low": close,
                     "close": close, "pre_close": close, "volume": 1000})
    return bars


def _base_from(bars: list[dict], score: float = 80.0) -> dict:
    """把行情包成"趋势分一直很高"的特征行，让状态机在第一天就切到上升。"""
    rows = [{"trade_date": bar["trade_date"], "trend_score": score, "state": "range"}
            for bar in bars]
    return {"SH600000": rows}


def _signal_index(bars: list[dict], params: dict | None = None) -> int:
    """这批数据上第一个"切到上升"的信号落在第几天（信号日收盘确认）。"""
    params = params or backtest.make_params(70, 1, 1)
    states = backtest.apply_params(_base_from(bars)["SH600000"], params)
    return next(index for index, row in enumerate(states)
                if row.get("state_switched") and row["state"] == "up")


class FillabilityTests(unittest.TestCase):
    """涨停买不进、跌停卖不掉。不检查这两条，回测会白送你最强势的那一段。"""

    def test_entry_is_skipped_when_next_day_is_limit_up(self):
        bars = _flat_bars()
        signal = _signal_index(bars)
        entry = signal + 1
        # 建仓日一字涨停：前收 10.00 → 涨停 11.00，买不进
        bars[entry] = {**bars[entry], "close": 11.0, "high": 11.0, "low": 11.0}
        payload = {"SH600000": bars}
        fwd = backtest.forward_arrays(payload, names={"SH600000": "测试股"})
        self.assertIsNone(fwd["SH600000"][5][signal])
        mask = backtest.fillable_mask(payload, names={"SH600000": "测试股"})
        self.assertFalse(mask["SH600000"][signal])
        self.assertTrue(mask["SH600000"][0])              # 别的日子正常

    def test_growth_board_signal_is_not_blocked_by_a_main_board_limit(self):
        bars = _flat_bars()
        signal = _signal_index(bars)
        bars[signal + 1] = {**bars[signal + 1], "close": 11.0, "high": 11.0, "low": 11.0}
        # 创业板 +10% 不是涨停，这天照样能买
        fwd = backtest.forward_arrays({"SZ300001": bars})
        self.assertIsNotNone(fwd["SZ300001"][5][signal])

    def test_exit_is_delayed_until_the_limit_down_opens(self):
        bars = _flat_bars()
        signal = _signal_index(bars)
        exit_pos = signal + 1 + 5
        # 到期日跌停封死（前收 10.00 → 9.00），第二天才打开
        bars[exit_pos] = {**bars[exit_pos], "close": 9.0, "high": 9.0, "low": 9.0, "pre_close": 10.0}
        bars[exit_pos + 1] = {**bars[exit_pos + 1], "close": 9.5, "high": 9.5, "low": 9.5, "pre_close": 9.0}
        fwd = backtest.forward_arrays({"SH600000": bars})
        # 按跌停价 9.00 算会得到 −10%；顺着跌停顺延到 9.5 才是你真的能卖掉的价
        self.assertAlmostEqual(fwd["SH600000"][5][signal], (9.5 / 10.0 - 1) * 100, places=4)

    def test_unfillable_exit_is_dropped_not_guessed(self):
        bars = _flat_bars()
        signal = _signal_index(bars)
        for index in range(signal + 1 + 5, signal + 1 + 8):
            bars[index] = {**bars[index], "close": 9.0, "high": 9.0, "low": 9.0, "pre_close": 10.0}
        fwd = backtest.forward_arrays({"SH600000": bars}, delay_max=1)
        self.assertIsNone(fwd["SH600000"][5][signal])

    def test_limit_check_can_be_switched_off(self):
        bars = _flat_bars()
        signal = _signal_index(bars)
        bars[signal + 1] = {**bars[signal + 1], "close": 11.0, "high": 11.0, "low": 11.0}
        fwd = backtest.forward_arrays({"SH600000": bars}, limit_check=False)
        self.assertIsNotNone(fwd["SH600000"][5][signal])

    def test_evaluate_counts_blocked_signals_separately(self):
        bars = _flat_bars()
        signal = _signal_index(bars)
        bars[signal + 1] = {**bars[signal + 1], "close": 11.0, "high": 11.0, "low": 11.0}
        payload = {"SH600000": bars}
        base = _base_from(bars)
        fwd = backtest.forward_arrays(payload)
        mask = backtest.fillable_mask(payload)
        result = backtest.evaluate(payload, base, fwd, backtest.make_params(70, 1, 1),
                                   fillable=mask)
        self.assertEqual(result["signals"], 0)            # 唯一的信号买不进
        self.assertEqual(result["blocked"], 1)


class CostTests(unittest.TestCase):
    """成本：信号和基准都扣同一笔，所以它压的是绝对收益和胜率，不是超额。"""

    def test_cost_lowers_returns_and_can_flip_a_win_into_a_loss(self):
        bars = _flat_bars()
        signal = _signal_index(bars)
        bars[signal + 1 + 5] = {**bars[signal + 1 + 5], "close": 10.01}   # +0.1%，比往返成本还薄
        payload = {"SH600000": bars}
        base = _base_from(bars)
        fwd = backtest.forward_arrays(payload)
        params = backtest.make_params(70, 1, 1)
        plain = backtest.evaluate(payload, base, fwd, params)
        net = backtest.evaluate(payload, base, fwd, params, cost=0.102)
        self.assertAlmostEqual(plain["avg5"], 0.1, places=3)
        self.assertAlmostEqual(net["avg5"], 0.1 - 0.102, places=3)
        self.assertEqual(plain["win5"], 100.0)
        self.assertEqual(net["win5"], 0.0)                # 赚 0.1% 扣完成本是亏的

    def test_excess_is_unaffected_when_both_sides_pay_the_same_cost(self):
        bars = _flat_bars()
        signal = _signal_index(bars)
        bars[signal + 1 + 5] = {**bars[signal + 1 + 5], "close": 10.01}
        payload = {"SH600000": bars}
        base = _base_from(bars)
        fwd = backtest.forward_arrays(payload)
        params = backtest.make_params(70, 1, 1)
        plain = backtest.evaluate(payload, base, fwd, params)
        net = backtest.evaluate(payload, base, fwd, params, cost=0.102)
        # 同一笔成本从两边一起扣，超额不变——真正吃掉收益的是"触发次数 × 成本"
        self.assertAlmostEqual(plain["excess5"], net["excess5"], places=6)

    def test_cost_is_read_from_config(self):
        cfg = {"backtest": {"cost": {"commission_bps": 2.5, "stamp_bps": 5.0, "transfer_bps": 0.1}}}
        self.assertAlmostEqual(backtest.cost_pct(cfg), 0.102, places=4)
        self.assertAlmostEqual(backtest.cost_pct({}), 0.102, places=4)     # 缺配置用默认值
        self.assertAlmostEqual(backtest.cost_pct({"backtest": {"cost": {"stamp_bps": 0}}}), 0.052, places=4)


class PlateauTests(unittest.TestCase):
    """参数高原：孤立的尖峰多半是拟合，连成一片才值得用。"""

    GRID = {"enter_up": (60, 70, 80), "confirm_days": (1, 2), "min_state_days": (1, 3)}

    def _results(self, values: dict, grid: dict | None = None) -> list[dict]:
        grid = grid or self.GRID
        return [
            {"params": {"enter_up": e, "confirm_days": c, "min_state_days": m},
             "excess20": values.get((e, c, m), 1.0)}
            for e in grid["enter_up"]
            for c in grid["confirm_days"]
            for m in grid["min_state_days"]
        ]

    def test_neighbourhood_averages_self_and_surrounding_cells(self):
        table = backtest.plateau(self._results({(70, 1, 1): 6.0}), self.GRID)
        spike = table[(70, 1, 1)]
        self.assertEqual(spike["n"], 11)                  # 3×2×2 网格里除自己以外的 11 格
        self.assertAlmostEqual(spike["mean"], (6.0 + 11 * 1.0) / 12, places=4)
        self.assertEqual(spike["worst"], 1.0)             # 周围全是 1.0 → 高原塌了
        self.assertEqual(spike["positive"], 100.0)
        self.assertEqual(spike["own"], 6.0)

    def test_edge_cell_uses_the_neighbours_that_exist(self):
        table = backtest.plateau(self._results({}), self.GRID)
        corner = table[(60, 1, 1)]
        # 3×2×2 的角上：三个维度各只剩 2 个取值，去掉自己还剩 2×2×2−1 = 7 个邻居
        self.assertEqual(corner["n"], 7)
        self.assertAlmostEqual(corner["mean"], 1.0, places=6)

    def test_single_cell_grid_has_no_plateau(self):
        grid = {"enter_up": (70,), "confirm_days": (2,), "min_state_days": (3,)}
        table = backtest.plateau(self._results({}, grid), grid)
        self.assertEqual(table[(70, 2, 3)]["n"], 0)
        self.assertIsNone(table[(70, 2, 3)]["mean"])


class UniverseTests(unittest.TestCase):
    """标的池：成交额门槛 + 固定种子抽样（回测要能复现）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.cfg = load_config(Path(__file__).resolve().parents[1] / "config.yaml", project_root=root)
        cls.cfg["_db_path"] = str(root / "market.db")
        cls.conn = db.connect(cls.cfg["_db_path"])
        db.init_db(cls.conn, Path(__file__).resolve().parents[1] / "schema.sql")

        bars = []
        for code, amount in (("SH600000", 200_000_000), ("SZ000001", 150_000_000),
                             ("SZ300001", 5_000_000), ("SH000300", None)):
            for index in range(140):
                close = 10.0 + index * 0.01
                bars.append({
                    "code": code,
                    "trade_date": (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
                    "open": close, "high": close, "low": close, "close": close,
                    "volume": 1000, "amount": amount, "pct_chg": 0.1,
                })
        db.upsert_rows(cls.conn, "bars_daily", bars, ["code", "trade_date"])
        db.upsert_rows(cls.conn, "instruments", [
            {"code": "SH600000", "name": "大票甲", "type": "stock"},
            {"code": "SZ000001", "name": "大票乙", "type": "stock"},
            {"code": "SZ300001", "name": "小票", "type": "stock"},
            {"code": "SH000300", "name": "沪深300", "type": "index"},
        ], ["code"])

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def test_small_cap_is_dropped(self):
        picked = universe.select_codes(self.conn, self.cfg, min_bars=80)
        self.assertNotIn("SZ300001", picked["codes"])
        self.assertEqual(picked["dropped_liquidity"], 1)

    def test_index_is_not_judged_by_amount(self):
        """指数没有成交额可比，所以默认**不进池子**——不是免检，是不参与。"""
        picked = universe.select_codes(self.conn, self.cfg, min_bars=80)
        self.assertNotIn("SH000300", picked["codes"])
        self.assertEqual(picked["dropped_index"], 1)

        self.cfg["universe"]["include_index"] = True
        try:
            opened = universe.select_codes(self.conn, self.cfg, min_bars=80)
        finally:
            self.cfg["universe"]["include_index"] = False
        self.assertIn("SH000300", opened["codes"])        # 打开开关才进池，且过流动性免检
        self.assertEqual(opened["dropped_index"], 0)

    def test_threshold_zero_keeps_everything(self):
        picked = universe.select_codes(self.conn, {"universe": {"min_avg_amount_60d": 0}}, min_bars=80)
        self.assertIn("SZ300001", picked["codes"])

    def test_sampling_is_repeatable_with_a_fixed_seed(self):
        first = universe.select_codes(self.conn, self.cfg, min_bars=80, limit=1, seed=7)
        second = universe.select_codes(self.conn, self.cfg, min_bars=80, limit=1, seed=7)
        self.assertEqual(first["codes"], second["codes"])
        self.assertEqual(len(first["codes"]), 1)
        self.assertTrue(first["sampled"])


if __name__ == "__main__":
    unittest.main()
