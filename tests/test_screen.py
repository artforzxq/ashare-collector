"""全市场筛选：条件判定、扫描、落库的离线测试。"""

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from collector import db, rules, screen
from collector.config import load_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MatchRuleTests(unittest.TestCase):
    """默认条件（config 里的那套）的边界。规则本身在 tests/test_rules.py 里测。"""

    def _match(self, key: str, row: dict) -> bool:
        from collector import rules

        item = next(c for c in screen.DEFAULT_CRITERIA if c["key"] == key)
        return rules.matches(row, item["when"])

    def test_consolidation_needs_high_position_and_shrinking_volume(self):
        base = {"state": "range", "close": 11.0, "ma60": 10.0,
                "consolidation_days": 30, "vol_shrink_ratio": 0.5}
        self.assertTrue(self._match("蓄势", base))
        self.assertFalse(self._match("蓄势", {**base, "vol_shrink_ratio": 0.9}))     # 没缩量
        self.assertFalse(self._match("蓄势", {**base, "close": 10.05}))              # 不在高位
        self.assertFalse(self._match("蓄势", {**base, "consolidation_days": 5}))     # 横盘不够久
        self.assertFalse(self._match("蓄势", {**base, "state": "down"}))             # 下跌趋势不算蓄势

    def test_breakout_and_spike(self):
        self.assertTrue(self._match("突破", {"breakout_confirmed": 1}))
        self.assertFalse(self._match("突破", {"breakout_confirmed": 0}))
        # 异动已按阴阳拆成两条（见 VolumeSpikeSplitTests），这里只验量能那一档
        spike = {"vol_ratio_20": 3.2, "candle": {"body_pct": 4.0}}
        self.assertTrue(self._match("放量阳线异动", spike))
        self.assertFalse(self._match("放量阳线异动", {**spike, "vol_ratio_20": 2.9}))

    def test_trend_and_pullback(self):
        up = {"state": "up", "trend_score": 80.0}
        self.assertTrue(self._match("趋势", up))
        self.assertFalse(self._match("趋势", {"state": "range", "trend_score": 80.0}))
        self.assertTrue(self._match("回踩", {**up, "raw_values": {"dist_to_level": -1.2}}))
        self.assertFalse(self._match("回踩", {**up, "raw_values": {"dist_to_level": -4.0}}))
        self.assertFalse(self._match("回踩", {"state": "range", "raw_values": {"dist_to_level": -1.0}}))

    def test_new_low_base_criterion(self):
        """低位横盘：横得够久 + 缩量 + 收盘在 MA60 下方 + 不是上升趋势。"""
        base = {"state": "range", "close": 9.0, "ma60": 10.0,
                "consolidation_days": 25, "vol_shrink_ratio": 0.6}
        self.assertTrue(self._match("低位横盘", base))
        self.assertFalse(self._match("低位横盘", {**base, "close": 11.0}))       # 在均线上方
        self.assertFalse(self._match("低位横盘", {**base, "state": "up"}))


class ConfigDrivenCriteriaTests(unittest.TestCase):
    def test_criteria_come_from_config(self):
        cfg = {"screen": {"criteria": [
            {"key": "我的形态", "title": "自定义", "sort": "close",
             "when": [{"field": "state", "op": "==", "value": "up"}]}
        ]}}
        items = screen.criteria(cfg)
        self.assertEqual([item["key"] for item in items], ["我的形态"])
        self.assertEqual(items[0]["title"], "自定义")

    def test_disabled_criteria_are_skipped(self):
        cfg = {"screen": {"criteria": [
            {"key": "关掉的", "when": [{"field": "state", "op": "==", "value": "up"}], "enabled": False},
            {"key": "开着的", "when": [{"field": "state", "op": "==", "value": "up"}]},
        ]}}
        self.assertEqual([item["key"] for item in screen.criteria(cfg)], ["开着的"])

    def test_falls_back_to_builtin_when_config_is_empty(self):
        keys = [item["key"] for item in screen.criteria({})]
        self.assertIn("反转", keys)
        self.assertIn("低位横盘", keys)


class ScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=root)
        cls.cfg["_db_path"] = str(root / "t.db")
        cls.conn = db.connect(cls.cfg["_db_path"])
        db.init_db(cls.conn, PROJECT_ROOT / "schema.sql")

        bars = []
        for index in range(140):
            price = 10.0 + (0.05 if index % 2 else -0.05)      # 长期窄幅震荡
            bars.append({
                "code": "SH600000", "trade_date": f"2026-{1 + index // 28:02d}-{index % 28 + 1:02d}",
                "open": price, "high": round(price * 1.004, 3), "low": round(price * 0.996, 3),
                "close": price, "volume": 100000, "amount": 80000000, "pct_chg": 0.1,
                "source": "baostock",
            })
        last = bars[-1]["trade_date"]                          # 最后一根：放量突破
        bars.append({
            "code": "SH600000", "trade_date": "2026-06-01",
            "open": 10.2, "high": 10.9, "low": 10.1, "close": 10.8,
            "volume": 600000, "amount": 480000000, "pct_chg": 7.0, "source": "baostock",
        })
        db.upsert_rows(cls.conn, "bars_daily", bars, ["code", "trade_date"])
        db.upsert_rows(cls.conn, "instruments",
                       [{"code": "SH600000", "name": "浦发银行", "type": "stock",
                         "exchange": "SH", "in_watchlist": 0}], ["code"])

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def test_scan_finds_breakout_and_spike(self):
        result = screen.scan(self.conn, self.cfg, verbose=False)
        self.assertTrue(result["ok"])
        self.assertEqual(result["scanned"], 1)
        groups = result["groups"]
        self.assertGreaterEqual(len(groups["突破"]), 1)
        # 放量突破那根是阳线 → 落在"放量阳线异动"，不会跑到阴线那一组
        self.assertGreaterEqual(len(groups["放量阳线异动"]), 1)
        self.assertEqual(len(groups["放量阴线异动"]), 0)
        hit = groups["突破"][0]
        self.assertEqual(hit["code"], "SH600000")
        self.assertEqual(hit["name"], "浦发银行")        # 名称来自代码表

    def test_save_then_load_round_trip(self):
        result = screen.scan(self.conn, self.cfg, verbose=False)
        saved = screen.save(self.conn, result, top=5)
        self.assertGreater(saved, 0)
        loaded = screen.load(self.conn, {})
        self.assertEqual(loaded["trade_date"], result["trade_date"])
        self.assertIn("突破", loaded["groups"])
        self.assertEqual(loaded["groups"]["突破"][0]["code"], "SH600000")
        self.assertIsNotNone(loaded["last_run"])         # 运行留痕，页面能区分"没跑过"

    def test_scan_reports_empty_when_no_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=Path(tmp))
            cfg["_db_path"] = str(Path(tmp) / "empty.db")
            conn = db.connect(cfg["_db_path"])
            db.init_db(conn, PROJECT_ROOT / "schema.sql")
            result = screen.scan(conn, cfg, verbose=False)
            conn.close()
            self.assertFalse(result["ok"])
            self.assertIn("先跑", result["message"])


class OutcomeBackfillTests(unittest.TestCase):
    """筛出来的票后来怎么样了：回填口径必须和提醒复盘一致（信号日收盘 → 次日收盘建仓）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=root)
        cls.cfg["_db_path"] = str(root / "t.db")
        cls.conn = db.connect(cls.cfg["_db_path"])
        db.init_db(cls.conn, PROJECT_ROOT / "schema.sql")

        bars = []
        for index in range(40):
            close = 100.0 + index                       # 每天涨 1 元，方便手算
            bars.append({
                "code": "SH600000",
                "trade_date": (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
                "open": close, "high": close, "low": close, "close": close,
                "volume": 1000, "amount": 200_000_000, "pct_chg": 1.0,
            })
        db.upsert_rows(cls.conn, "bars_daily", bars, ["code", "trade_date"])
        db.upsert_rows(cls.conn, "screen_results", [
            {"trade_date": bars[10]["trade_date"], "criterion": "突破", "rank_no": 1,
             "code": "SH600000", "name": "测试股", "close": 110.0, "created_at": "2026-01-11 15:00:00"},
            {"trade_date": bars[-1]["trade_date"], "criterion": "蓄势", "rank_no": 1,
             "code": "SH600000", "name": "测试股", "close": 139.0, "created_at": "2026-02-09 15:00:00"},
        ], ["trade_date", "criterion", "code"])

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def test_outcome_uses_next_day_close_entry(self):
        # 用例之间共用同一个库，先把自己关心的两列清空，免得依赖执行顺序
        self.conn.execute("UPDATE screen_results SET outcome_5d=NULL, outcome_20d=NULL")
        self.conn.commit()
        info = screen.backfill_outcomes(self.conn)
        self.assertEqual(info["screen"], 1)              # 最后一天那条还不满持有期，不写
        row = db.query_one(
            self.conn,
            "SELECT outcome_5d, outcome_20d FROM screen_results WHERE criterion='突破'",
        )
        # 信号在 11 日 → 次日（12 日，收 111）建仓 → 第 5 个交易日收 116 / 第 20 个收 131
        self.assertAlmostEqual(row["outcome_5d"], (116 / 111 - 1) * 100, places=3)
        self.assertAlmostEqual(row["outcome_20d"], (131 / 111 - 1) * 100, places=3)
        pending = db.query_one(
            self.conn,
            "SELECT outcome_20d FROM screen_results WHERE criterion='蓄势'",
        )
        self.assertIsNone(pending["outcome_20d"])         # 还看不出结果，不硬填

    def test_backfill_is_idempotent(self):
        screen.backfill_outcomes(self.conn)
        again = screen.backfill_outcomes(self.conn)
        self.assertEqual(again["screen"], 0)             # 值没变就不再写

    def test_performance_report_reads_the_backfilled_rows(self):
        screen.backfill_outcomes(self.conn)
        text = screen.performance_report(self.conn)
        self.assertIn("筛选结果回填", text)
        self.assertIn("突破", text)


class LiquidityFilterTests(unittest.TestCase):
    """小票过滤：成交额不够的票根本不进筛选，而不是排在后面。

    "幽灵票"在回测里最危险——它们没有资金参与，形态是噪声，却常常在事后涨得最猛，
    一旦进样本就会把结论往乐观的方向拉。
    """

    def _scan_with_amounts(self, big: float, small: float):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=root)
        cfg["_db_path"] = str(root / "t.db")
        conn = db.connect(cfg["_db_path"])
        db.init_db(conn, PROJECT_ROOT / "schema.sql")
        bars = []
        for code, amount in (("SH600000", big), ("SZ300001", small)):
            for index in range(140):
                price = 10.0 + (0.05 if index % 2 else -0.05)
                bars.append({
                    "code": code, "trade_date": f"2026-{1 + index // 28:02d}-{index % 28 + 1:02d}",
                    "open": price, "high": round(price * 1.004, 3), "low": round(price * 0.996, 3),
                    "close": price, "volume": 100000, "amount": amount, "pct_chg": 0.1,
                })
        db.upsert_rows(conn, "bars_daily", bars, ["code", "trade_date"])
        db.upsert_rows(conn, "instruments", [
            {"code": "SH600000", "name": "大票", "type": "stock"},
            {"code": "SZ300001", "name": "小票", "type": "stock"},
        ], ["code"])
        result = screen.scan(conn, cfg, verbose=False)
        conn.close()
        tmp.cleanup()
        return result

    def test_small_cap_is_dropped_from_the_scan(self):
        result = self._scan_with_amounts(big=200_000_000, small=5_000_000)
        self.assertTrue(result["ok"])
        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["universe"]["dropped_liquidity"], 1)

    def test_threshold_from_config_can_be_disabled(self):
        result = self._scan_with_amounts(big=2_000_000, small=1_000_000)
        # 两只都没过 3000 万门槛 → 一个都不剩
        self.assertFalse(result["ok"])
        self.assertIn("成交额", result["message"])


class PrimaryLabelTests(unittest.TestCase):
    """多标签不删，但每只票要有一个"从哪一条开始看"的主标签。"""

    def _items(self):
        return [{"key": "突破", "priority": 10.0}, {"key": "异动", "priority": 50.0},
                {"key": "趋势", "priority": 90.0}]

    def test_lowest_priority_number_wins(self):
        groups = {"突破": [{"code": "SH600000"}], "异动": [{"code": "SH600000"}],
                  "趋势": [{"code": "SH600000"}]}
        primary = screen.primary_labels(self._items(), groups)
        self.assertEqual(primary["SH600000"], "突破")

    def test_single_label_stock_is_its_own_primary(self):
        groups = {"趋势": [{"code": "SH600001"}]}
        self.assertEqual(screen.primary_labels(self._items(), groups)["SH600001"], "趋势")

    def test_priority_defaults_to_config_order(self):
        cfg = {"screen": {"criteria": [
            {"key": "先写的", "when": [{"field": "close", "op": ">", "value": 0}]},
            {"key": "后写的", "when": [{"field": "close", "op": ">", "value": 0}]},
        ]}}
        items = screen.criteria(cfg)
        self.assertLess(items[0]["priority"], items[1]["priority"])


class VolumeSpikeSplitTests(unittest.TestCase):
    """异动拆成阳线 / 阴线：同样是放量 3 倍，一个是资金进场，一个是派发。"""

    def _match(self, key: str, row: dict) -> bool:
        item = next(c for c in screen.criteria({}) if c["key"] == key)
        return rules.matches(row, item["when"])

    def test_bullish_spike_only_matches_the_yang_bucket(self):
        row = {"vol_ratio_20": 4.0, "candle": {"body_pct": 5.0}}
        self.assertTrue(self._match("放量阳线异动", row))
        self.assertFalse(self._match("放量阴线异动", row))

    def test_bearish_spike_only_matches_the_yin_bucket(self):
        row = {"vol_ratio_20": 4.0, "candle": {"body_pct": -3.0}}
        self.assertFalse(self._match("放量阳线异动", row))
        self.assertTrue(self._match("放量阴线异动", row))

    def test_quiet_day_matches_neither(self):
        row = {"vol_ratio_20": 1.2, "candle": {"body_pct": 5.0}}
        self.assertFalse(self._match("放量阳线异动", row))
        self.assertFalse(self._match("放量阴线异动", row))

    def test_flat_close_counts_as_yang(self):
        """收平（实体 0）算阳线那一档——不然它会从两个条件里同时漏掉。"""
        row = {"vol_ratio_20": 3.0, "candle": {"body_pct": 0.0}}
        self.assertTrue(self._match("放量阳线异动", row))
        self.assertFalse(self._match("放量阴线异动", row))


class ReplayTests(unittest.TestCase):
    """历史重放：把筛选条件放到过去每一天，立刻拿到 5 / 20 日的真实表现。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=root)
        cls.cfg["_db_path"] = str(root / "t.db")
        # 只留一个"永远成立"的条件，日期和收益都能手算
        cls.cfg["screen"]["criteria"] = [
            {"key": "全部", "sort": "close", "when": [{"field": "close", "op": ">", "value": 0}]},
        ]
        cls.conn = db.connect(cls.cfg["_db_path"])
        db.init_db(cls.conn, PROJECT_ROOT / "schema.sql")
        cls.bars = []
        for index in range(140):
            close = 100.0 + index
            cls.bars.append({
                "code": "SH600000",
                "trade_date": (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
                "open": close, "high": close, "low": close, "close": close,
                "volume": 1000, "amount": 200_000_000, "pct_chg": 1.0,
            })
        db.upsert_rows(cls.conn, "bars_daily", cls.bars, ["code", "trade_date"])
        db.upsert_rows(cls.conn, "instruments",
                       [{"code": "SH600000", "name": "浦发银行", "type": "stock",
                         "exchange": "SH", "in_watchlist": 0}], ["code"])

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def _dates(self):
        return [bar["trade_date"] for bar in self.bars]

    def setUp(self):
        # 同类里的用例共用一份库，谁先跑都得有重放结果在
        if not db.query(self.conn, "SELECT 1 AS x FROM screen_results LIMIT 1"):
            screen.replay(self.conn, self.cfg, days=30, verbose=False)

    def test_writes_one_row_per_replayed_day(self):
        result = screen.replay(self.conn, self.cfg, days=30, verbose=False)
        self.assertTrue(result["ok"])
        self.assertEqual(result["days"], 30)
        self.assertEqual(result["written"], 30)
        rows = db.query(self.conn, "SELECT COUNT(*) AS n FROM screen_results")
        self.assertEqual(rows[0]["n"], 30)

    def test_row_carries_that_days_price_not_the_latest_one(self):
        """重放的第 1 天必须写那天的收盘价——这是"没有未来函数"的直接体现。"""
        row = db.query(self.conn, "SELECT trade_date, close, rank_no FROM screen_results "
                                  "ORDER BY trade_date LIMIT 1")[0]
        index = self._dates().index(row["trade_date"])
        self.assertAlmostEqual(row["close"], self.bars[index]["close"], places=3)
        self.assertEqual(row["rank_no"], 1)
        self.assertLess(row["close"], self.bars[-1]["close"])

    def test_forward_return_uses_next_day_entry(self):
        screen.backfill_outcomes(self.conn)
        row = db.query(self.conn, "SELECT trade_date, outcome_5d, outcome_20d FROM screen_results "
                                  "ORDER BY trade_date LIMIT 1")[0]
        index = self._dates().index(row["trade_date"])
        entry = self.bars[index + 1]["close"]                 # 次日收盘建仓
        expect5 = round((self.bars[index + 1 + 5]["close"] / entry - 1) * 100, 4)
        expect20 = round((self.bars[index + 1 + 20]["close"] / entry - 1) * 100, 4)
        self.assertAlmostEqual(row["outcome_5d"], expect5, places=3)
        self.assertAlmostEqual(row["outcome_20d"], expect20, places=3)

    def test_too_short_window_is_refused(self):
        result = screen.replay(self.conn, self.cfg, days=5, verbose=False)
        self.assertFalse(result["ok"])
        self.assertIn("20 个交易日", result["message"])

    def test_equal_weight_baseline_matches_the_only_symbol(self):
        """等权基准 = 同一批日期上"随便买一只同口径的票"的平均收益；这里只有一只票，手算得到。"""
        day = self.bars[-30]["trade_date"]
        baseline = screen.equal_weight_baseline(self.conn, self.cfg, [day])
        index = self._dates().index(day)
        entry = self.bars[index + 1]["close"]
        expect = round((self.bars[index + 1 + 20]["close"] / entry - 1) * 100, 4)
        self.assertAlmostEqual(baseline["mean"], expect, places=3)
        self.assertEqual(baseline["universe"], 1)

    def test_report_shows_the_market_wide_column_when_baseline_is_given(self):
        screen.backfill_outcomes(self.conn)
        day = self.bars[-30]["trade_date"]
        baseline = screen.equal_weight_baseline(self.conn, self.cfg, [day])
        plain = screen.performance_report(self.conn)
        with_base = screen.performance_report(self.conn, baseline=baseline)
        self.assertNotIn("vs全市场", plain)
        self.assertIn("vs全市场", with_base)

    def test_replay_of_the_last_day_matches_a_real_scan(self):
        """重放的最后一天必须和真跑一遍筛选写出来的一样，否则两边对不上。"""
        screen.replay(self.conn, self.cfg, days=30, verbose=False)
        last = db.query(self.conn, "SELECT MAX(trade_date) AS d FROM screen_results")[0]["d"]
        replayed = [dict(r) for r in db.query(
            self.conn, "SELECT * FROM screen_results WHERE trade_date=? ORDER BY criterion, code", (last,))]
        result = screen.scan(self.conn, self.cfg, verbose=False)
        screen.save(self.conn, result)
        scanned = [dict(r) for r in db.query(
            self.conn, "SELECT * FROM screen_results WHERE trade_date=? ORDER BY criterion, code", (last,))]
        strip = lambda rows: [{k: v for k, v in row.items() if k != "created_at"} for row in rows]
        self.assertEqual(strip(replayed), strip(scanned))


if __name__ == "__main__":
    unittest.main()
