"""全市场筛选：条件判定、扫描、落库的离线测试。"""

import tempfile
import unittest
from pathlib import Path

from collector import db, screen
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
        self.assertTrue(self._match("异动", {"vol_ratio_20": 3.2}))
        self.assertFalse(self._match("异动", {"vol_ratio_20": 2.9}))

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
                "close": price, "volume": 100000, "amount": 1000000, "pct_chg": 0.1,
                "source": "baostock",
            })
        last = bars[-1]["trade_date"]                          # 最后一根：放量突破
        bars.append({
            "code": "SH600000", "trade_date": "2026-06-01",
            "open": 10.2, "high": 10.9, "low": 10.1, "close": 10.8,
            "volume": 600000, "amount": 6000000, "pct_chg": 7.0, "source": "baostock",
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
        self.assertGreaterEqual(len(groups["异动"]), 1)
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


if __name__ == "__main__":
    unittest.main()
