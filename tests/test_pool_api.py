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

    # ---- 出池提示：只对"观察中的个股"、只提示不动作 ----

    def _seed_bars(self, code: str, days: list[str], amount: float = 120_000_000.0):
        db.upsert_rows(
            self.conn,
            "bars_daily",
            [{"code": code, "trade_date": day, "close": 10.0, "amount": amount,
              "quality_flag": "ok"} for day in days],
            ["code", "trade_date"],
        )

    def test_miss_streak_counts_consecutive_screen_days(self):
        # 三个筛选日：某只票只在最早那天出现过 → 连续未命中应该是 2（不是"总共缺席 2 次"这么巧，
        # 这里换一只在中间出现过的票来区分：连续是 1，总共缺席也是 1）
        for day in ("2026-09-15", "2026-09-16", "2026-09-17"):
            db.upsert_rows(self.conn, "screen_results", [{
                "trade_date": day, "criterion": "趋势", "rank_no": 1,
                "code": "SH600487" if day == "2026-09-15" else "SZ000333",
                "name": "示例", "close": 10.0, "pct_chg": 1.0, "state": "up",
                "trend_score": 80.0, "vol_ratio": 1.0, "detail": "",
            }], ["trade_date", "criterion", "code"])
        self.cfg["watchlist"] = {"indices": [], "etfs": [], "stocks": ["SH600487", "SZ000333"]}
        members = {item["code"]: item for item in server.App(self.cfg).pool()["members"]}
        self.assertEqual(members["SH600487"]["miss_streak"], 2)      # 最新两天都没它
        self.assertEqual(members["SZ000333"]["miss_streak"], 0)      # 最新一天有它

    def test_holdings_are_exempt_from_the_exit_hint(self):
        self._seed_screen()
        self._seed_bars("SZ000333", ["2026-09-18"], amount=1_000_000.0)   # 成交额跌破门槛
        self._seed_bars("SH600487", ["2026-09-18"], amount=1_000_000.0)
        self.cfg["watchlist"] = {"indices": [], "etfs": [], "stocks": ["SZ000333", "SH600487"],
                                 "holdings": ["SZ000333"]}
        members = {item["code"]: item for item in server.App(self.cfg).pool()["members"]}
        self.assertTrue(members["SZ000333"]["held"])
        self.assertFalse(members["SZ000333"]["exit_hint"])           # 持仓豁免：理由照列，但不提示
        self.assertTrue(members["SZ000333"]["exit_reasons"])
        self.assertFalse(members["SH600487"]["held"])
        self.assertTrue(members["SH600487"]["exit_hint"])
        self.assertIn("成交额", "；".join(members["SH600487"]["exit_reasons"]))

    def test_index_and_etf_never_get_the_hint(self):
        self._seed_screen()
        self._seed_bars("SH510500", ["2026-09-18"], amount=1_000_000.0)
        self.cfg["watchlist"] = {"indices": [], "etfs": ["SH510500"], "stocks": []}
        member = server.App(self.cfg).pool()["members"][0]
        self.assertEqual(member["role"], "观察")
        self.assertTrue(member["exit_reasons"])                       # 证据照算
        self.assertFalse(member["exit_hint"])                         # 但 ETF 不参与轮换

    def test_stale_screen_turns_every_hint_off(self):
        """筛选两周没跑，尺子就没量准——这时候任何"该出池"都是假警报。"""
        self._seed_screen()
        late = ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-28"]
        self._seed_bars("SH600487", late, amount=1_000_000.0)
        self.cfg["watchlist"] = {"indices": [], "etfs": [], "stocks": ["SH600487"]}
        payload = server.App(self.cfg).pool()
        self.assertTrue(payload["screen_stale"])
        self.assertEqual(payload["screen_gap_days"], 6)
        self.assertFalse(payload["members"][0]["exit_hint"])

    def test_fresh_screen_keeps_the_hint_on(self):
        self._seed_screen()
        self._seed_bars("SH600487", ["2026-09-18"], amount=1_000_000.0)
        self.cfg["watchlist"] = {"indices": [], "etfs": [], "stocks": ["SH600487"]}
        payload = server.App(self.cfg).pool()
        self.assertFalse(payload["screen_stale"])
        self.assertTrue(payload["members"][0]["exit_hint"])


if __name__ == "__main__":
    unittest.main()
