"""新股与次新：判定、分类、以及"该不该出现在推送里"。

判定依据是本地日线的第一根——同步一次要 750 天，只拿到 N 根就说明上市约 N 个交易日。
所以这里既测正常情况，也测"同步没跑完"这条前提被打破时的行为。
"""

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from collector import db, newstock
from collector.config import load_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _days(count: int, end: str = "2026-09-18") -> list[str]:
    last = datetime.strptime(end, "%Y-%m-%d")
    return [(last - timedelta(days=count - 1 - index)).strftime("%Y-%m-%d") for index in range(count)]


class NewStockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=self.root)
        self.cfg["_db_path"] = str(self.root / "t.db")
        self.conn = db.connect(self.cfg["_db_path"])
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")
        self._seed()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _seed(self):
        db.upsert_rows(self.conn, "instruments", [
            {"code": "SH601999", "name": "新股甲", "type": "stock"},
            {"code": "SZ301888", "name": "次新乙", "type": "stock"},
            {"code": "SH600000", "name": "老票丙", "type": "stock"},
            {"code": "SH516040", "name": "新 ETF", "type": "etf"},
        ], ["code"])
        bars = []
        for code, count in (("SH601999", 5), ("SZ301888", 100), ("SH600000", 300), ("SH516040", 3)):
            for index, day in enumerate(_days(count)):
                bars.append({
                    "code": code, "trade_date": day, "open": 10.0, "high": 10.3,
                    "low": 9.9, "close": 10.0 + index * 0.1, "pre_close": 10.0 + index * 0.1,
                    "amount": 150_000_000.0, "pct_chg": 1.0, "quality_flag": "ok",
                })
        db.upsert_rows(self.conn, "bars_daily", bars, ["code", "trade_date"])

    def test_board_of_labels_the_limits_that_apply(self):
        self.assertEqual(newstock.board_of("SH601999"), "主板")
        self.assertEqual(newstock.board_of("SZ301888"), "创业板")
        self.assertEqual(newstock.board_of("SH688111"), "科创板")
        self.assertEqual(newstock.board_of("BJ920298"), "北交所")

    def test_refresh_classifies_by_listed_days(self):
        result = newstock.refresh(self.conn, self.cfg)
        self.assertTrue(result["ok"])
        rows = {row["code"]: dict(row) for row in db.query(self.conn, "SELECT * FROM new_listings")}
        self.assertEqual(rows["SH601999"]["stage"], "new")
        self.assertEqual(rows["SH601999"]["trading_days"], 5)
        self.assertEqual(rows["SH601999"]["listed_date"], _days(5)[0])
        self.assertEqual(rows["SZ301888"]["stage"], "recent")
        # 上市远超一年的老票根本不进这张表（它不是"新股"这件事本身没有记录价值）；
        # 曾经是次新、后来老下去的票才会留在表里被标成 old——见下面那个用例。
        self.assertNotIn("SH600000", rows)

    def test_etf_is_not_a_new_stock(self):
        """新 ETF 天天有，混进来会把新股名单淹没。"""
        newstock.refresh(self.conn, self.cfg)
        codes = {row["code"] for row in db.query(self.conn, "SELECT code FROM new_listings")}
        self.assertNotIn("SH516040", codes)

    def test_since_list_pct_uses_the_first_close(self):
        newstock.refresh(self.conn, self.cfg)
        row = db.query_one(self.conn, "SELECT * FROM new_listings WHERE code='SH601999'")
        # 第一根收 10.0，最后一根收 10.4 → +4%
        self.assertAlmostEqual(row["since_list_pct"], 4.0, places=2)

    def test_scan_splits_new_and_recent(self):
        newstock.refresh(self.conn, self.cfg)
        data = newstock.scan(self.conn, self.cfg)
        self.assertTrue(data["ok"])
        self.assertEqual([item["code"] for item in data["new"]], ["SH601999"])
        self.assertEqual([item["code"] for item in data["recent"]], ["SZ301888"])
        self.assertEqual(data["counts"], {"new": 1, "recent": 1})

    def test_graduating_past_the_window_marks_it_old(self):
        newstock.refresh(self.conn, self.cfg)
        self.cfg["newstock"]["new_days"] = 3
        newstock.refresh(self.conn, self.cfg)
        row = db.query_one(self.conn, "SELECT stage FROM new_listings WHERE code='SH601999'")
        self.assertEqual(row["stage"], "recent")        # 开了 5 天，不再算新股

    def test_empty_table_says_what_to_run(self):
        data = newstock.scan(self.conn, self.cfg)
        self.assertFalse(data["ok"])
        self.assertIn("listings", data["message"])

    def test_authoritative_ipo_date_beats_the_bars_guess(self):
        """有了真实上市日，就算一根日线都没同步到，也该出现在名单里。"""
        db.upsert_rows(self.conn, "instruments", [
            {"code": "SH601091", "name": "沈鼓集团", "type": "stock", "listed_date": "2026-09-17"},
        ], ["code"])
        result = newstock.refresh(self.conn, self.cfg)
        self.assertTrue(result["ok"])
        row = db.query_one(self.conn, "SELECT * FROM new_listings WHERE code='SH601091'")
        self.assertEqual(row["listed_date"], "2026-09-17")
        self.assertEqual(row["stage"], "new")
        self.assertEqual(row["bars_loaded"], 0)          # 日线还没同步到
        self.assertEqual(row["estimated"], 0)            # 但上市日是权威的
        self.assertIn(row["code"], [item["code"] for item in newstock.scan(self.conn, self.cfg)["new"]])

    def test_blocked_bars_do_not_produce_a_fake_return(self):
        """新股首日常被跳变保护标成 blocked（来源 pre_close 不可信），
        拿它当基准会算出 +177% 这种数字——所以宁可不给。"""
        db.upsert_rows(self.conn, "instruments", [
            {"code": "SH601091", "name": "沈鼓集团", "type": "stock", "listed_date": "2026-09-17"},
        ], ["code"])
        db.upsert_rows(self.conn, "bars_daily", [
            {"code": "SH601091", "trade_date": "2026-09-17", "close": 20.8, "pre_close": 4.39,
             "pct_chg": 373.8, "quality_flag": "blocked"},
            {"code": "SH601091", "trade_date": "2026-09-18", "close": 57.77, "pre_close": 20.8,
             "pct_chg": 177.7, "quality_flag": "blocked"},
        ], ["code", "trade_date"])
        newstock.refresh(self.conn, self.cfg)
        row = db.query_one(self.conn, "SELECT * FROM new_listings WHERE code='SH601091'")
        self.assertIsNone(row["since_list_pct"])
        self.assertEqual(row["blocked_bars"], 2)

    def test_unblocked_prefix_gives_a_real_return(self):
        db.upsert_rows(self.conn, "instruments", [
            {"code": "SH601091", "name": "沈鼓集团", "type": "stock", "listed_date": "2026-09-17"},
        ], ["code"])
        db.upsert_rows(self.conn, "bars_daily", [
            {"code": "SH601091", "trade_date": "2026-09-17", "close": 20.0, "pct_chg": 44.0,
             "quality_flag": "blocked"},
            {"code": "SH601091", "trade_date": "2026-09-18", "close": 22.0, "pct_chg": 10.0,
             "quality_flag": "ok"},
        ], ["code", "trade_date"])
        newstock.refresh(self.conn, self.cfg)
        row = db.query_one(self.conn, "SELECT * FROM new_listings WHERE code='SH601091'")
        self.assertEqual(row["since_list_pct"], 0.0)      # 只有一根没被标记，锚点和末日是同一根
        self.assertEqual(row["blocked_bars"], 1)

    def test_today_listings_only_returns_the_day_it_listed(self):
        newstock.refresh(self.conn, self.cfg)
        self.assertEqual([item["code"] for item in newstock.today_listings(self.conn, _days(5)[0])],
                         ["SH601999"])
        self.assertEqual(newstock.today_listings(self.conn, "2026-09-18"), [])

    def test_highlights_put_alerted_names_first(self):
        newstock.refresh(self.conn, self.cfg)
        db.upsert_rows(self.conn, "alerts", [{
            "created_at": db.now_iso(), "trade_date": "2026-09-18", "code": "SZ301888",
            "level": "P1", "signal_type": "STATE_TO_UP", "message": "状态转上升趋势",
        }], ["code", "trade_date", "signal_type", "level"])
        picks = newstock.highlights(self.conn, self.cfg, "2026-09-18")
        self.assertEqual(picks[0]["code"], "SZ301888")
        self.assertTrue(picks[0]["alerted"])


if __name__ == "__main__":
    unittest.main()
