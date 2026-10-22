"""情绪阶段：规则边界 + 影子定位（不驱动任何决策）。"""

import tempfile
import unittest
from pathlib import Path

from collector import db, sentiment, weekly

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def row(day, boards, up, broken=0, ratio=0.5, amount=1e12):
    return {"trade_date": day, "max_boards": boards, "limit_up_count": up,
            "limit_down_count": 5, "broken_limit_count": broken,
            "up_ratio": ratio, "total_amount": amount}


class SentimentClassifyTests(unittest.TestCase):
    def test_short_history_is_undetermined(self):
        result = sentiment.classify([row("2026-10-01", 3, 50)])
        self.assertEqual(result["sentiment_stage"], "未定")
        self.assertIsNone(result["sentiment_score"])

    def test_missing_boards_is_undetermined_not_zero(self):
        history = [row(f"2026-10-0{i}", 3, 30) for i in range(1, 6)]
        history.append({**row("2026-10-06", 0, 30), "max_boards": None})
        result = sentiment.classify(history)
        self.assertEqual(result["sentiment_stage"], "未定")
        self.assertIn("缺最高连板", result["sentiment_note"])

    def test_ice_point(self):
        history = [row(f"2026-10-0{i}", 2, 20, ratio=0.4) for i in range(1, 6)]
        history.append(row("2026-10-06", 1, 15, ratio=0.35))
        self.assertEqual(sentiment.classify(history)["sentiment_stage"], "冰点")

    def test_ebb_tide_needs_leaders_to_break_and_limits_to_fade(self):
        history = [row(f"2026-10-0{i}", 6, 60, ratio=0.55) for i in range(1, 6)]
        history.append(row("2026-10-06", 3, 30, broken=30, ratio=0.4))
        self.assertEqual(sentiment.classify(history)["sentiment_stage"], "退潮")

    def test_climax(self):
        history = [row(f"2026-10-0{i}", 4, 30, ratio=0.5) for i in range(1, 6)]
        history.append(row("2026-10-06", 5, 90, ratio=0.65))
        self.assertEqual(sentiment.classify(history)["sentiment_stage"], "高潮")

    def test_fermentation(self):
        history = [row(f"2026-10-0{i}", 4, 10, ratio=0.5) for i in range(1, 6)]
        history.append(row("2026-10-06", 3, 5, ratio=0.55))
        self.assertEqual(sentiment.classify(history)["sentiment_stage"], "发酵")


class SentimentStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_update_writes_only_market_breadth(self):
        rows = [{"trade_date": f"2026-10-0{i}", "max_boards": 4, "limit_up_count": 50,
                 "limit_down_count": 3, "broken_limit_count": 10, "up_ratio": 0.55,
                 "total_amount": 1e12, "coverage": 5000} for i in range(1, 7)]
        db.upsert_rows(self.conn, "market_breadth", rows, ["trade_date"])
        result = sentiment.update(self.conn, {}, "2026-10-06")
        self.assertIn(result["sentiment_stage"], sentiment.STAGES + ("未定",))
        stored = db.query_one(self.conn, "SELECT sentiment_stage FROM market_breadth WHERE trade_date='2026-10-06'")
        self.assertEqual(stored["sentiment_stage"], result["sentiment_stage"])
        # 影子定位：不写 alerts，也不写 features
        self.assertEqual(db.query(self.conn, "SELECT * FROM alerts"), [])
        self.assertEqual(db.query(self.conn, "SELECT * FROM features_daily"), [])

    def test_weekly_card_says_what_is_missing(self):
        rows = [{"trade_date": f"2026-10-0{i}", "max_boards": 4, "limit_up_count": 50,
                 "limit_down_count": 3, "broken_limit_count": 10, "up_ratio": 0.55,
                 "total_amount": 1e12, "coverage": 5000} for i in range(1, 7)]
        db.upsert_rows(self.conn, "market_breadth", rows, ["trade_date"])
        sentiment.backfill(self.conn, {})
        text = weekly.report(weekly.build(self.conn, {}))
        self.assertIn("周度环境卡", text)
        self.assertIn("缺：行业分类", text)
        self.assertIn("三道闸门", text)

    def test_latest_available_premium_is_not_today_when_next_day_is_missing(self):
        db.upsert_rows(self.conn, "market_breadth", [
            {"trade_date": "2026-10-21", "limit_up_next_avg": -2.5,
             "limit_up_next_count": 30, "first_board_next_avg": -3.0,
             "first_board_next_count": 20},
            {"trade_date": "2026-10-22", "limit_up_next_avg": None,
             "limit_up_next_count": 0},
        ], ["trade_date"])
        row = sentiment.latest_available_premium(self.conn, "2026-10-22")
        self.assertEqual(row["trade_date"], "2026-10-21")
        self.assertAlmostEqual(row["limit_up_next_avg"], -2.5)


if __name__ == "__main__":
    unittest.main()
