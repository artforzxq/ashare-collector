"""单票体检：各层的数据要能拼到一张纸上，缺哪一层都不能崩。"""

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from collector import db, dig
from collector.config import load_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=root)
        cls.cfg["_db_path"] = str(root / "t.db")
        cls.conn = db.connect(cls.cfg["_db_path"])
        db.init_db(cls.conn, PROJECT_ROOT / "schema.sql")
        # 一只"池外"的票：features_daily 里没有行，体检要能就地算
        bars = []
        for index in range(140):
            close = 10.0 + index * 0.05
            bars.append({
                "code": "SH600001",
                "trade_date": (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
                "open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
                "volume": 100000, "amount": 200_000_000, "pct_chg": 0.5,
            })
        db.upsert_rows(cls.conn, "bars_daily", bars, ["code", "trade_date"])
        db.upsert_rows(cls.conn, "instruments", [
            {"code": "SH600001", "name": "测试银行", "type": "stock", "exchange": "SH"},
        ], ["code"])

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def test_code_is_normalised(self):
        """600001 / sh600001 / 600001.SH 都要认。"""
        for raw in ("600001", "sh600001", "600001.SH"):
            data = dig.collect(self.conn, self.cfg, raw)
            self.assertTrue(data["ok"], raw)
            self.assertEqual(data["code"], "SH600001")

    def test_out_of_pool_symbol_gets_features_computed_on_the_spot(self):
        data = dig.collect(self.conn, self.cfg, "SH600001")
        self.assertTrue(data["live_feature"])          # 池外：现算的
        self.assertIsNotNone(data["feature"])
        self.assertIsNotNone(data["risk"])             # 风险层也能算出来

    def test_layers_are_assembled_without_missing_sections(self):
        text = dig.report(dig.collect(self.conn, self.cfg, "SH600001"))
        for title in ("① 行情", "② 状态层", "③ 位置与结构", "④ 形态与关键位",
                      "⑤ 风险层", "⑥ 提醒", "⑦ 筛选命中"):
            self.assertIn(title, text)
        self.assertIn("测试银行", text)                # 名称来自代码表

    def test_unknown_code_says_what_to_do(self):
        data = dig.collect(self.conn, self.cfg, "999999")
        self.assertFalse(data["ok"])
        self.assertIn("add", data["message"])          # 告诉用户怎么加进来

    def test_layer_tables_can_be_empty(self):
        """没有提醒、没有筛选命中、没有因子贡献，都要能出报告而不是崩。"""
        data = dig.collect(self.conn, self.cfg, "SH600001")
        self.assertEqual(data["alerts"], [])
        self.assertEqual(data["contributions"], [])
        text = dig.report(data)
        self.assertIn("没有提醒记录", text)
        self.assertIn("一次都没被筛出来", text)


class BucketPositionTests(unittest.TestCase):
    """分档对照：把一只票的当前读数接到全市场历史统计上。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=root)
        cls.cfg["_db_path"] = str(root / "t.db")
        cls.conn = db.connect(cls.cfg["_db_path"])
        db.init_db(cls.conn, PROJECT_ROOT / "schema.sql")
        bars = []
        for code, base in (("SH600001", 10.0), ("SH600002", 30.0)):
            for index in range(160):
                close = base + index * 0.05
                bars.append({
                    "code": code,
                    "trade_date": (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
                    "open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
                    "volume": 100000, "amount": 200_000_000, "pct_chg": 0.3,
                })
        db.upsert_rows(cls.conn, "bars_daily", bars, ["code", "trade_date"])

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def test_report_mentions_the_bucket_and_its_history(self):
        text = dig.bucket_position(self.conn, self.cfg, "SH600001", days=60)
        self.assertIn("落在哪一档", text)
        self.assertIn("超额", text)

    def test_unknown_symbol_is_reported_not_crashed(self):
        text = dig.bucket_position(self.conn, self.cfg, "SH999999", days=60)
        self.assertTrue(text.startswith("（"))


if __name__ == "__main__":
    unittest.main()
