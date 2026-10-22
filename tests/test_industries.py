"""行业分类：导入、代码归一、覆盖率门限。"""

import tempfile
import unittest
from pathlib import Path

from collector import db, industries

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class IndustryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = db.connect(self.root / "t.db")
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")
        db.upsert_rows(self.conn, "instruments", [
            {"code": "SH600000", "name": "浦发银行", "type": "stock", "exchange": "SH"},
            {"code": "SZ000001", "name": "平安银行", "type": "stock", "exchange": "SZ"},
            {"code": "SH510300", "name": "沪深300ETF", "type": "etf", "exchange": "SH"},
        ], ["code"])

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_csv_import_and_coverage(self):
        path = self.root / "industry.csv"
        path.write_text("代码,名称,行业\n600000,浦发银行,银行\n000001,平安银行,银行\n510300,沪深300ETF,宽基\n",
                        encoding="utf-8")
        result = industries.import_csv(self.conn, path)
        self.assertEqual(result["written"], 3)
        # ETF 不应计入个股覆盖率
        cov = industries.coverage(self.conn)
        self.assertEqual(cov["stocks"], 2)
        self.assertEqual(cov["tagged"], 2)
        self.assertEqual(cov["industries"], 1)
        self.assertAlmostEqual(cov["coverage"], 100.0, places=1)
        hist = industries.history_coverage(self.conn)
        self.assertEqual(hist["snapshots"], 1)
        self.assertEqual(hist["rows"], 3)

    def test_headerless_csv(self):
        path = self.root / "industry.txt"
        path.write_text("600000,银行\n000001,银行\n", encoding="utf-8")
        industries.import_csv(self.conn, path)
        mapping = industries.mapping(self.conn)
        self.assertEqual(mapping["SH600000"], "银行")
        self.assertEqual(mapping["SZ000001"], "银行")

    def test_low_coverage_says_do_not_draw_conclusions(self):
        # 只标 1/2 个行业 → 50%，必须明确拒绝给板块结论
        path = self.root / "one.csv"
        path.write_text("code,industry\n600000,银行\n", encoding="utf-8")
        industries.import_csv(self.conn, path)
        text = industries.report(self.conn)
        self.assertIn("覆盖不足 80%", text)
        self.assertIn("industries --sync", text)

    def test_bad_csv_is_a_readable_error(self):
        path = self.root / "empty.csv"
        path.write_text("", encoding="utf-8")
        with self.assertRaises(industries.IndustryError):
            industries.import_csv(self.conn, path)

    def test_asof_mapping_uses_snapshot_and_falls_back(self):
        industries.apply_rows(self.conn, [{"code": "600000", "industry": "银行"}],
                              "test", snapshot_date="2026-01-01")
        industries.apply_rows(self.conn, [{"code": "600000", "industry": "金融"}],
                              "test", snapshot_date="2026-02-01")
        early, snap = industries.mapping_asof(self.conn, "2026-01-15")
        self.assertEqual(snap, "2026-01-01")
        self.assertEqual(early["SH600000"], "银行")
        late, snap = industries.mapping_asof(self.conn, "2026-03-01")
        self.assertEqual(snap, "2026-02-01")
        self.assertEqual(late["SH600000"], "金融")
        fallback, snap = industries.mapping_asof(self.conn, "2025-12-01")
        self.assertIsNone(snap)                      # 没有历史 → 当前映射，明确近似
        self.assertEqual(fallback["SH600000"], "金融")


if __name__ == "__main__":
    unittest.main()
