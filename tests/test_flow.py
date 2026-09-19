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

    def test_mixed_sources_are_not_compared(self):
        """同一段序列不能混源：拿甲家今天减乙家昨天，算出来的是两家口径的差，
        不是申购赎回（深交所 63.29 亿 vs 东财 63.41 亿，差的就是这个）。"""
        db.upsert_rows(self.conn, "etf_shares", [
            {"code": "SH510300", "trade_date": "2026-09-17", "shares": 100e8, "nav": 4.0, "source": "sse"},
            {"code": "SH510300", "trade_date": "2026-09-18", "shares": 101e8, "nav": 4.0, "source": "akshare"},
        ], ["code", "trade_date"])
        self.conn.commit()
        snap = flow.snapshot(self.conn, self.cfg, "2026-09-18")
        self.assertIn("SH510300", snap["mixed_source"])
        self.assertIsNone(snap["broad_etf"]["inflow"])

    def test_empty_database_says_what_to_run(self):
        snap = flow.snapshot(self.conn, self.cfg)
        self.assertFalse(snap["ok"])
        self.assertIn("3-每日任务", snap["message"])

    def test_detail_ranks_inflows_and_outflows(self):
        self._shares("SH510300", 100e8, 110e8)          # +40 亿
        self._shares("SH512880", 50e8, 40e8)            # −40 亿
        self.conn.commit()
        detail = flow.etf_detail(self.conn, self.cfg, "2026-09-18")
        self.assertEqual([item["code"] for item in detail["inflow_top"]], ["SH510300"])
        self.assertEqual([item["code"] for item in detail["outflow_top"]], ["SH512880"])
        self.assertAlmostEqual(detail["inflow_top"][0]["inflow"], 40.0, places=2)
        self.assertAlmostEqual(detail["inflow_top"][0]["shares_pct"], 10.0, places=2)

    def test_divergence_says_when_it_cannot_tell(self):
        result = flow.divergence(self.conn, self.cfg)
        self.assertFalse(result["ok"])
        self.assertIn("背离", result["message"])


class ConcentrationTests(unittest.TestCase):
    """集中度：缩量大涨时区分"存量抱团"还是"增量进场"。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=Path(self.tmp.name))
        self.cfg["_db_path"] = str(Path(self.tmp.name) / "t.db")
        self.cfg["market"] = {"concentration": {"window": 60, "top": 5, "big_amount": 1e9}}
        self.conn = db.connect(self.cfg["_db_path"])
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _seed(self, amounts_by_day: dict[str, list[float]]):
        rows, instruments = [], []
        for day, amounts in amounts_by_day.items():
            for index, amount in enumerate(amounts):
                code = f"SH{600000 + index}"
                rows.append({"code": code, "trade_date": day, "close": 10.0, "amount": amount,
                             "pct_chg": 1.0, "quality_flag": "ok"})
                instruments.append({"code": code, "name": f"票{index}", "type": "stock"})
        db.upsert_rows(self.conn, "instruments", instruments, ["code"])
        db.upsert_rows(self.conn, "bars_daily", rows, ["code", "trade_date"])
        self.conn.commit()

    def test_concentrated_day_has_a_higher_share_in_the_top_names(self):
        flat = [1e8] * 1200                       # 1200 只，每只 1 亿：很分散
        self._seed({"2026-09-17": flat, "2026-09-18": [1e11] + [1e8] * 1199})
        result = flow.concentration(self.conn, self.cfg, "2026-09-18")
        self.assertTrue(result["ok"])
        self.assertGreater(result["top100_pct"], 45)          # 一只独大（其余 1199 只都很小）
        self.assertEqual(result["top10"][0]["amount"], 1000.0)  # 1e11 / 1e8

    def test_thin_sample_gives_no_conclusion(self):
        self._seed({"2026-09-18": [1e8] * 50})               # 样本太少
        result = flow.concentration(self.conn, self.cfg, "2026-09-18")
        self.assertFalse(result["ok"])
        self.assertIn("集中度", result["message"])

    def test_percentile_needs_history(self):
        self._seed({"2026-09-18": [1e8] * 1200})
        result = flow.concentration(self.conn, self.cfg, "2026-09-18")
        self.assertTrue(result["ok"])
        self.assertIsNone(result["top100_rank"])              # 只有一天，没有分位可比


if __name__ == "__main__":
    unittest.main()
