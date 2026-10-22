"""主线识别：四关全过才算主线；单票暴动不能伪装成板块行情。"""

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from collector import db, mainline

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MainlineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")
        start = date(2026, 1, 1)
        days = [(start + timedelta(days=index)).isoformat() for index in range(130)]
        instruments = []
        bars = []
        for index in range(6):
            code = f"SH60000{index}"
            instruments.append({"code": code, "name": f"A{index}", "type": "stock",
                                "exchange": "SH", "industry": "行业A",
                                "industry_classification": "test", "industry_updated": "2026-01-01"})
            for step, day in enumerate(days):
                close = 10.0 * (1.02 ** step)
                bars.append({"code": code, "trade_date": day, "close": close,
                             "high": close * 1.001, "amount": 1e8 * (1 + step * 0.01)})
        for index in range(6):
            code = f"SZ00000{index}"
            instruments.append({"code": code, "name": f"B{index}", "type": "stock",
                                "exchange": "SZ", "industry": "行业B",
                                "industry_classification": "test", "industry_updated": "2026-01-01"})
            for day in days:
                bars.append({"code": code, "trade_date": day, "close": 10.0,
                             "high": 10.05, "amount": 1e8})
        db.upsert_rows(self.conn, "instruments", instruments, ["code"])
        db.upsert_rows(self.conn, "bars_daily", bars, ["code", "trade_date"])

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_rising_industry_with_flow_and_diffusion_is_mainline(self):
        result = mainline.build(self.conn, {})
        top = result["industries"][0]
        self.assertEqual(top["industry"], "行业A")
        self.assertEqual(top["verdict"], "主线候选")
        self.assertGreater(top["diffusion"], 90)
        self.assertGreater(top["share_trend_pp"], 0)
        self.assertGreaterEqual(top["top_weeks"], 3)
        self.assertIn("主线候选", mainline.report(result))
        # 行业B 价格不强，不能因为只有两个行业就被算成主线
        b = next(item for item in result["industries"] if item["industry"] == "行业B")
        self.assertNotEqual(b["verdict"], "主线候选")

    def test_low_industry_coverage_refuses_to_draw_conclusions(self):
        self.conn.execute("UPDATE instruments SET industry=NULL WHERE code LIKE 'SZ%'")
        self.conn.commit()
        with self.assertRaises(mainline.MainlineError):
            mainline.build(self.conn, {})


if __name__ == "__main__":
    unittest.main()
