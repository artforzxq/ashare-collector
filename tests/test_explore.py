"""分档研究：口径、超额算法、以及"维度写错要能看懂"。"""

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from collector import db, explore
from collector.config import load_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=root)
        cls.cfg["_db_path"] = str(root / "t.db")
        cls.conn = db.connect(cls.cfg["_db_path"])
        db.init_db(cls.conn, PROJECT_ROOT / "schema.sql")
        # 两只票，都是每天涨 1 元：等于给"同一天全样本等权"一个手算得出来的基准
        bars = []
        for code, base in (("SH600000", 100.0), ("SH600001", 50.0)):
            for index in range(120):
                close = base + index
                bars.append({
                    "code": code,
                    "trade_date": (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
                    "open": close, "high": close, "low": close, "close": close,
                    "volume": 1000, "amount": 200_000_000, "pct_chg": 1.0,
                })
        db.upsert_rows(cls.conn, "bars_daily", bars, ["code", "trade_date"])

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def test_panel_has_every_dimension_and_forward_returns(self):
        frame = explore.load_panel(self.conn, days=30, horizon=20)
        for spec in explore.FIELDS.values():
            self.assertIn(spec["column"], frame.columns)
        self.assertGreater(frame["fwd"].notna().sum(), 0)

    def test_excess_is_relative_to_the_same_day(self):
        """两只票每天都涨 1 元 → 同一天两只是同一个涨幅 → 每一档的超额都应该是 0。"""
        frame = explore.load_panel(self.conn, days=30, horizon=20)
        table, _ = explore.bucket_table(frame, "成交额")
        for _, row in table.iterrows():
            if row["样本"]:
                self.assertAlmostEqual(row["超额"], 0.0, places=6)

    def test_forward_return_is_next_day_entry(self):
        frame = explore.load_panel(self.conn, days=30, horizon=20)
        row = frame.dropna(subset=["fwd"]).iloc[0]
        code = row["code"]
        closes = db.query(
            self.conn,
            "SELECT trade_date, close FROM bars_daily WHERE code=? ORDER BY trade_date",
            (code,),
        )
        dates = [item["trade_date"] for item in closes]
        index = dates.index(row["trade_date"])
        entry = closes[index + 1]["close"]
        exit_ = closes[index + 1 + 20]["close"]
        self.assertAlmostEqual(row["fwd"], (exit_ / entry - 1) * 100, places=6)

    def test_unknown_field_says_what_is_available(self):
        frame = explore.load_panel(self.conn, days=30, horizon=20)
        with self.assertRaises(explore.ExploreError) as caught:
            explore.bucket_table(frame, "不存在的维度")
        self.assertIn("成交额", str(caught.exception))

    def test_report_mentions_the_gradient_rule(self):
        frame = explore.load_panel(self.conn, days=30, horizon=20)
        text = explore.report(frame, fields=["位置"], with_cross=False)
        self.assertIn("【位置】", text)
        self.assertIn("超额", text)
        self.assertIn("梯度", text)

    def test_cross_table_rejects_unknown_dimension(self):
        frame = explore.load_panel(self.conn, days=30, horizon=20)
        with self.assertRaises(explore.ExploreError):
            explore.cross_table(frame, "位置", "没有这个")


if __name__ == "__main__":
    unittest.main()
