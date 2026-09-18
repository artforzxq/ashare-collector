"""观察池候选清单：进池子的证据、排序规则、以及池外票"就地算特征"这条兜底。"""

import tempfile
import unittest
from pathlib import Path

from collector import candidates, db
from collector.config import load_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCREEN_DATE = "2026-06-01"


def _flat_bars(code: str, days: int = 140, amount: float = 80_000_000) -> list[dict]:
    rows = []
    for index in range(days):
        price = 10.0 + (0.05 if index % 2 else -0.05)      # 长期窄幅震荡
        rows.append({
            "code": code, "trade_date": f"2026-{1 + index // 28:02d}-{index % 28 + 1:02d}",
            "open": price, "high": round(price * 1.004, 3), "low": round(price * 0.996, 3),
            "close": price, "volume": 100000, "amount": amount, "pct_chg": 0.1,
            "source": "baostock",
        })
    return rows


def _breakout_bar(code: str, amount: float = 80_000_000) -> dict:
    return {
        "code": code, "trade_date": SCREEN_DATE,
        "open": 10.2, "high": 10.9, "low": 10.1, "close": 10.8,
        "volume": 600000, "amount": amount * 6, "pct_chg": 7.0, "source": "baostock",
    }


def _screen_row(code: str, criterion: str, rank: int = 1, name: str | None = None) -> dict:
    return {
        "trade_date": SCREEN_DATE, "criterion": criterion, "rank_no": rank,
        "code": code, "name": name or code, "close": 10.8, "pct_chg": 7.0,
        "state": "up", "trend_score": 72.0, "vol_ratio": 3.4, "detail": "放量突破",
        "created_at": "2026-06-01T16:00:00",
    }


class CandidateBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=root)
        cls.cfg["_db_path"] = str(root / "t.db")
        cls.conn = db.connect(cls.cfg["_db_path"])
        db.init_db(cls.conn, PROJECT_ROOT / "schema.sql")

        bars = []
        for code, amount in (("SH600100", 200_000_000), ("SH600200", 1_000_000),
                             ("SH600300", 200_000_000)):
            bars.extend(_flat_bars(code, amount=amount))
            bars.append(_breakout_bar(code, amount=amount))
        db.upsert_rows(cls.conn, "bars_daily", bars, ["code", "trade_date"])

        db.upsert_rows(cls.conn, "screen_results", [
            _screen_row("SH600100", "突破", 1, "流动好的票"),
            _screen_row("SH600100", "异动", 2, "流动好的票"),
            _screen_row("SH600200", "突破", 1, "买不进的票"),
            _screen_row("SH600200", "异动", 2, "买不进的票"),
            _screen_row("SH600300", "突破", 1, "只命中一次"),
            _screen_row("SZ000333", "趋势", 1, "已在池子里"),
            _screen_row("SZ000333", "回踩", 1, "已在池子里"),
        ], ["trade_date", "criterion", "code"])

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def setUp(self):
        self.result = candidates.build(self.conn, self.cfg, days=5)

    def test_pool_members_are_not_offered_as_candidates(self):
        """已经在池子里的票不该再被推荐一次。"""
        self.assertTrue(self.result["ok"])
        codes = [item["code"] for item in self.result["candidates"]]
        self.assertNotIn("SZ000333", codes)
        self.assertEqual([item["code"] for item in self.result["members"]], ["SZ000333"])

    def test_single_hit_is_not_a_candidate(self):
        """只被命中一次的是噪声，不该进候选名单。"""
        codes = [item["code"] for item in self.result["candidates"]]
        self.assertNotIn("SH600300", codes)
        self.assertIn("SH600100", codes)

    def test_liquid_candidate_ranks_before_illiquid_one(self):
        codes = [item["code"] for item in self.result["candidates"]]
        self.assertLess(codes.index("SH600100"), codes.index("SH600200"))
        illiquid = next(item for item in self.result["candidates"] if item["code"] == "SH600200")
        self.assertFalse(illiquid["liquid"])

    def test_out_of_pool_code_gets_features_computed_on_the_spot(self):
        """池外的票在 features_daily 里没有行——不能因此把仓位一栏空着。"""
        item = next(i for i in self.result["candidates"] if i["code"] == "SH600100")
        self.assertIsNotNone(item["cap"])
        self.assertIsNotNone(item["state"])
        self.assertIsNotNone(item["risk_reward"])

    def test_report_has_a_copy_paste_line(self):
        text = candidates.report(self.result, cfg=self.cfg)
        self.assertIn("python run.py add", text)
        self.assertIn("SH600100", text)
        self.assertIn("池内现状", text)

    def test_window_and_missing_codes_are_reported(self):
        self.assertEqual(self.result["window"], [SCREEN_DATE])
        self.assertIn("SZ399006", self.result["pooled_missing"])


class NoScreenResultsTests(unittest.TestCase):
    def test_empty_warehouse_says_what_to_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=root)
            cfg["_db_path"] = str(root / "empty.db")
            conn = db.connect(cfg["_db_path"])
            db.init_db(conn, PROJECT_ROOT / "schema.sql")
            result = candidates.build(conn, cfg)
            conn.close()
        self.assertFalse(result["ok"])
        self.assertIn("screen", result["message"])


class SortKeyTests(unittest.TestCase):
    """排序规则本身：能建仓的在前，买得进的在前，然后才比证据。"""

    def _item(self, code, cap, liquid, days, criteria):
        return {"code": code, "cap": cap, "liquid": liquid, "days": days,
                "criteria": criteria, "hits": days, "avg_amount_60d": 1.0}

    def test_tradable_candidates_come_first(self):
        zero = self._item("SH600001", 0.0, True, 5, ["突破"])
        positive = self._item("SH600002", 0.2, True, 1, ["突破"])
        self.assertLess(candidates._sort_key(positive), candidates._sort_key(zero))

    def test_more_days_outweighs_more_criteria_on_one_day(self):
        persistent = self._item("SH600003", 0.2, True, 3, ["突破"])
        one_day = self._item("SH600004", 0.2, True, 1, ["突破", "异动", "趋势"])
        self.assertLess(candidates._sort_key(persistent), candidates._sort_key(one_day))


if __name__ == "__main__":
    unittest.main()
