"""资金去向：四把尺子（宽基 ETF / 行业主题 ETF / 成交额 / 融资余额）与规则化读法。

这一层的价值全在**口径诚实**上，所以测试盯的也是口径：
  · 只有"前一天也有份额"的标的才参与净流入，算不出的单独列出来，不拿 0 冒充"没变化"；
  · 深市 ETF 只有"最新份额"（交易所接口如此），不能拿它算"较前一日"；
  · 读法是**规则**，不预测、不给买卖建议。
"""

import tempfile
import unittest
from pathlib import Path

from collector import db, flow
from collector.config import load_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class VerdictTests(unittest.TestCase):
    """读法是纯函数：给它四个读数，它只能描述方向，不能编故事。"""

    def test_broad_out_sector_in_is_rotation(self):
        text = flow.verdict({"broad_etf": {"inflow": -10.0}, "sector_etf": {"inflow": 20.0}})
        self.assertIn("挪向行业", text)

    def test_both_in_is_incremental_money(self):
        text = flow.verdict({"broad_etf": {"inflow": 5.0}, "sector_etf": {"inflow": 5.0}})
        self.assertIn("增量资金", text)

    def test_margin_and_amount_are_appended(self):
        text = flow.verdict({
            "broad_etf": {"inflow": -1.0}, "sector_etf": {"inflow": -1.0},
            "amount": {"amount_ratio": 0.9}, "margin": {"delta": 6.0},
        })
        self.assertIn("没放量", text)
        self.assertIn("加杠杆", text)

    def test_missing_inputs_do_not_pretend(self):
        self.assertIn("数据不足", flow.verdict({}))


class SnapshotTests(unittest.TestCase):
    CFG = {"market": {"broad_etfs": ["SH510300"]}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=Path(self.tmp.name))
        self.cfg["_db_path"] = str(Path(self.tmp.name) / "t.db")
        self.cfg["market"] = {"broad_etfs": ["SH510300"]}
        self.conn = db.connect(self.cfg["_db_path"])
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")
        db.upsert_rows(self.conn, "instruments", [
            {"code": "SH510300", "name": "沪深300ETF", "type": "etf"},
            {"code": "SH512880", "name": "证券ETF", "type": "etf"},
        ], ["code"])
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _shares(self, code, prev, today):
        db.upsert_rows(self.conn, "etf_shares", [
            {"code": code, "trade_date": "2026-09-17", "shares": prev, "nav": 4.0},
            {"code": code, "trade_date": "2026-09-18", "shares": today, "nav": 4.0},
        ], ["code", "trade_date"])

    def test_broad_and_sector_are_split(self):
        self._shares("SH510300", 100e8, 100.5e8)        # 宽基：+0.5 亿份 × 4 元 = +2 亿
        self._shares("SH512880", 50e8, 51e8)            # 行业：+1 亿份 × 4 元 = +4 亿
        self.conn.commit()
        snap = flow.snapshot(self.conn, self.cfg, "2026-09-18")
        self.assertTrue(snap["ok"])
        self.assertAlmostEqual(snap["broad_etf"]["inflow"], 2.0, places=2)
        self.assertAlmostEqual(snap["sector_etf"]["inflow"], 4.0, places=2)
        self.assertIn("增量资金", snap["verdict"])

    def test_a_code_without_a_previous_day_is_not_counted_as_zero(self):
        """只有一天份额的标的不能算"没变化"——那是"不知道"。"""
        db.upsert_rows(self.conn, "etf_shares", [
            {"code": "SH510300", "trade_date": "2026-09-18", "shares": 100e8, "nav": 4.0},
        ], ["code", "trade_date"])
        self.conn.commit()
        snap = flow.snapshot(self.conn, self.cfg, "2026-09-18")
        self.assertIsNone(snap["broad_etf"]["inflow"])

    def test_missing_price_is_reported_not_silently_dropped(self):
        self._shares("SH510300", 100e8, 101e8)
        self.conn.execute("UPDATE etf_shares SET nav=NULL WHERE code='SH510300'")
        self.conn.commit()
        snap = flow.snapshot(self.conn, self.cfg, "2026-09-18")
        self.assertIn("SH510300", snap["unpriced"])
        self.assertIsNone(snap["broad_etf"]["inflow"])

    def test_empty_database_says_what_to_run(self):
        snap = flow.snapshot(self.conn, self.cfg)
        self.assertFalse(snap["ok"])
        self.assertIn("3-每日任务", snap["message"])


if __name__ == "__main__":
    unittest.main()
