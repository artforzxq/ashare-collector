"""筛选池接口：候选进池 / 池内现状 / 形态表现，三段拼在一个只读接口里。

页面不该自己发明口径——该不该进池子、按什么排序，都由 candidates.build 决定，
这个接口只负责把结果和上下文（门槛、上次扫描、回填表现）凑到一处。
"""

import tempfile
import unittest
from pathlib import Path

from collector import db, server
from collector.config import load_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PoolApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=self.root)
        self.cfg["watchlist"] = {"indices": [], "etfs": [], "stocks": ["SZ000333"]}
        self.cfg["_db_path"] = str(self.root / "t.db")
        self.conn = db.connect(self.cfg["_db_path"])
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")
        self.app = server.App(self.cfg)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _seed_screen(self):
        rows = []
        for day in ("2026-09-17", "2026-09-18"):
            # 池外的票，两个筛选日都被命中 → 应该成为候选
            rows.append({"trade_date": day, "criterion": "回踩", "rank_no": 1, "code": "SH600487",
                         "name": "亨通光电", "close": 69.49, "pct_chg": 1.46, "state": "range",
                         "trend_score": 53.0, "vol_ratio": 1.35, "detail": "回踩支撑带",
                         "outcome_20d": 2.5})
            # 池内的票，只命中一次 → 出现在"池内现状"，不当候选
            if day == "2026-09-18":
                rows.append({"trade_date": day, "criterion": "趋势", "rank_no": 1, "code": "SZ000333",
                             "name": "美的集团", "close": 84.4, "pct_chg": -0.75, "state": "up",
                             "trend_score": 61.1, "vol_ratio": 0.9, "detail": "上升趋势",
                             "outcome_20d": None})
        db.upsert_rows(self.conn, "screen_results", rows, ["trade_date", "criterion", "code"])
        db.upsert_rows(self.conn, "data_health", [{
            "run_date": "2026-09-18", "source": "local", "task": "screen",
            "status": "ok", "rows": 1803, "error_msg": "命中 12 条",
        }], ["run_date", "source", "task"])

    def test_empty_database_says_what_to_run(self):
        payload = self.app.pool()
        self.assertFalse(payload["ok"])
        self.assertIn("筛选", payload["message"])

    def test_pool_splits_candidates_members_and_outcomes(self):
        self._seed_screen()
        payload = self.app.pool()
        self.assertTrue(payload["ok"], payload.get("message"))

        self.assertEqual([item["code"] for item in payload["candidates"]], ["SH600487"])
        candidate = payload["candidates"][0]
        self.assertEqual(candidate["hits"], 2)
        self.assertEqual(candidate["days"], 2)
        self.assertEqual(candidate["criteria"], ["回踩"])
        self.assertEqual(candidate["excess_20d"], 2.5)

        self.assertEqual([item["code"] for item in payload["members"]], ["SZ000333"])
        self.assertEqual(payload["pooled_missing"], [])          # 池内的票筛到过

        outcomes = {row["criterion"]: row for row in payload["outcomes"]}
        self.assertEqual(outcomes["回踩"]["n"], 2)
        self.assertEqual(outcomes["回踩"]["avg20"], 2.5)

    def test_pool_carries_the_context_that_makes_it_readable(self):
        self._seed_screen()
        payload = self.app.pool()
        self.assertEqual(payload["min_hits"], 2)
        self.assertEqual(payload["min_avg_amount"], 30_000_000)
        self.assertEqual(payload["last_run"]["scanned"], 1803)
        self.assertEqual(payload["trade_date"], "2026-09-18")
        self.assertEqual(payload["window"], ["2026-09-18", "2026-09-17"])

    def test_member_that_never_shows_up_is_flagged(self):
        """池子里的票最近一次都没被筛到——这是"该不该挪出去"的第一条证据。"""
        self._seed_screen()
        self.cfg["watchlist"] = {"indices": [], "etfs": [], "stocks": ["SZ000333", "SH600000"]}
        payload = server.App(self.cfg).pool()
        self.assertEqual(payload["pooled_missing"], ["SH600000"])


if __name__ == "__main__":
    unittest.main()
