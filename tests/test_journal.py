"""交易账本：开仓冻结、风险合并、平仓只补结果。"""

import json
import tempfile
import unittest
from pathlib import Path

from collector import db, journal, server

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = {"_db_path": str(self.root / "t.db"), "journal": {"limits": {}}}
        self.conn = db.connect(self.cfg["_db_path"])
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _open(self, **overrides):
        data = {
            "code": "SH600000", "framework": "short", "opened_on": "2026-10-20",
            "entry_price": 100.0, "stop_price": 92.0, "target_price": 124.0,
            "shares": 1000.0, "account_equity": 1_000_000.0,
            "thesis": "放量突破平台", "invalidation": "收盘跌破 92",
            "theme": "算力", "nonconsensus": "订单在四季度确认",
            "exit_plan": "回到核心指数",
        }
        data.update(overrides)
        return journal.open_trade(self.conn, self.cfg, **data)

    def test_open_computes_risk_position_and_reward_risk(self):
        result = self._open()
        trade = result["trade"]
        # R = 1000 × (100 − 92) = 8000；8000 ÷ 1,000,000 = 0.8%
        self.assertAlmostEqual(trade["risk_amount"], 8000.0, places=2)
        self.assertAlmostEqual(trade["risk_pct"], 0.8, places=4)
        self.assertAlmostEqual(trade["position_pct"], 10.0, places=4)
        self.assertAlmostEqual(trade["reward_risk"], 3.0, places=4)
        self.assertEqual(result["warnings"], [])

    def test_thesis_and_invalidation_are_required(self):
        with self.assertRaises(journal.JournalError):
            self._open(thesis="")
        with self.assertRaises(journal.JournalError):
            self._open(invalidation="")

    def test_code_is_normalized_to_exchange_prefix(self):
        result = self._open(code="600000")
        self.assertEqual(result["trade"]["code"], "SH600000")

    def test_duplicate_trade_is_refused(self):
        self._open()
        with self.assertRaises(journal.JournalError):
            self._open()

    def test_close_only_adds_result_and_refuses_double_close(self):
        opened = self._open()
        trade_id = opened["id"]
        before = dict(db.query_one(self.conn, "SELECT * FROM trades WHERE id=?", (trade_id,)))
        closed = journal.close_trade(
            self.conn, trade_id, exit_price=110.0, closed_on="2026-10-22",
            discipline=True, alpha_beta="alpha", note="按计划",
        )
        # 开仓字段一个字都不变
        for key in ("entry_price", "stop_price", "risk_amount", "thesis", "invalidation"):
            self.assertEqual(before[key], closed[key])
        self.assertAlmostEqual(closed["pnl"], 10_000.0, places=2)
        # 名义 100,000 × 0.302% = 302 元；净盈亏要扣掉它
        self.assertAlmostEqual(closed["cost_amount"], 302.0, places=2)
        self.assertAlmostEqual(closed["pnl_net"], 9_698.0, places=2)
        self.assertEqual(closed["discipline"], 1)
        with self.assertRaises(journal.JournalError):
            journal.close_trade(self.conn, trade_id, exit_price=111.0)

    def test_portfolio_risk_sums_and_flags_same_theme(self):
        self._open()
        self._open(code="SZ000001", theme="算力", shares=500.0)
        risk = journal.portfolio_risk(self.conn, self.cfg)
        # 第二笔 R = 500 × 8 = 4000 → 0.4%；合计 1.2%
        self.assertAlmostEqual(risk["total_risk_pct"], 1.2, places=4)
        self.assertAlmostEqual(risk["by_theme"]["算力"], 1.2, places=4)
        kinds = {(item["kind"], item["key"]) for item in risk["breaches"]}
        self.assertIn(("theme", "算力"), kinds)
        self.assertNotIn(("portfolio", "合计"), kinds)

    def test_api_returns_open_and_risk(self):
        self._open()
        payload = server.App(self.cfg).journal()
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["open"]), 1)
        self.assertAlmostEqual(payload["risk"]["total_risk_pct"], 0.8, places=4)
        status, _, body = server.dispatch(server.App(self.cfg), "GET", "/api/journal")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)["open"]), 1)

    def test_unlock_and_guardrails_come_from_closed_books(self):
        opened = self._open()
        journal.close_trade(self.conn, opened["id"], exit_price=95.0,
                            closed_on="2026-10-21", discipline=True, cfg=self.cfg)
        tier = journal.unlock(self.conn)
        self.assertEqual(tier["tier"], 5)          # 1 笔，离 30 笔还远
        self.assertEqual(tier["next_at"], 30)
        guard = journal.guardrails(self.conn)
        self.assertEqual(guard["consecutive_losses"], 1)
        self.assertEqual(guard["flags"], [])

    def test_missing_discipline_is_unknown_not_followed(self):
        opened = self._open()
        closed = journal.close_trade(self.conn, opened["id"], exit_price=101.0,
                                     closed_on="2026-10-22")
        self.assertIsNone(closed["discipline"])
        self.assertEqual(journal.closed_stats(self.conn)["violations"], 0)


if __name__ == "__main__":
    unittest.main()
