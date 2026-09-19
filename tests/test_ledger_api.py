"""页面上的两个新接口：/api/factors（因子台账）与 /api/backtest（参数回测结果）。

这两个接口的性质和 /api/freshness 一样：**只读、永远别抛异常**。
页面拿不到数据时最坏的结果应该是"显示一句人话"，而不是白屏。
"""

import json
import tempfile
import unittest
from pathlib import Path

from collector import backtest as backtest_mod
from collector import db, server
from collector.config import load_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class LedgerApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=self.root)
        self.cfg["_db_path"] = str(self.root / "t.db")
        self.conn = db.connect(self.cfg["_db_path"])
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")
        self.app = server.App(self.cfg)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    # ---- 因子台账 ----

    def test_empty_database_still_answers(self):
        payload = self.app.factors()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["items"], [])
        self.assertIsNone(payload["as_of"])
        self.assertIn("shadow_days", payload)

    def test_ledger_reports_coverage_ic_and_verdict(self):
        db.upsert_rows(
            self.conn,
            "factor_registry",
            [
                {"factor_id": "ma_slope", "name": "均线斜率", "layer": "state", "role": "primary",
                 "category": "trend", "weight": 0.25, "min_samples": 60, "status": "active"},
                {"factor_id": "adx", "name": "ADX", "layer": "state", "role": "modifier",
                 "category": "trend", "weight": 0.0, "min_samples": 60, "status": "shadow"},
            ],
            ["factor_id"],
        )
        rows = []
        for index, day in enumerate(("2026-09-01", "2026-09-02", "2026-09-03")):
            rows.append({"trade_date": day, "code": "SH600487", "factor_id": "ma_slope",
                         "normalized_score": 0.4 + index * 0.1, "weight": 0.25,
                         "contribution": 0.1, "feature_version": "v1"})
            rows.append({"trade_date": day, "code": "SH600487", "factor_id": "adx",
                         "normalized_score": 0.6, "weight": 0.0, "contribution": 0.0,
                         "feature_version": "v1"})
        db.upsert_rows(self.conn, "factor_contributions", rows, ["trade_date", "code", "factor_id"])

        payload = self.app.factors()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["as_of"], "2026-09-03")
        items = {item["factor_id"]: item for item in payload["items"]}
        self.assertEqual(items["ma_slope"]["days"], 3)
        self.assertEqual(items["ma_slope"]["status"], "active")
        self.assertEqual(items["adx"]["status"], "shadow")
        # 样本只有 3 天，按规格影子因子要 20 天：结论必须说"样本不足"，不能给个假 IC 结论
        self.assertIn("样本不足", items["adx"]["verdict"])

    # ---- 参数回测 ----

    def test_backtest_without_a_run_says_how_to_start(self):
        payload = self.app.backtest()
        self.assertFalse(payload["ok"])
        self.assertIn("还没跑过", payload["message"])
        self.assertTrue(payload["hint"])

    def test_backtest_reads_the_latest_saved_run(self):
        result = {
            "ok": True,
            "results": [
                {"label": "enter_up=65 确认2日 最短1日", "params": {"enter_up": 65, "confirm_days": 2, "min_state_days": 1},
                 "signals": 120, "blocked": 3, "win20": 52.5, "avg20": 1.4, "base20": 0.6, "excess20": 0.8,
                 "mdd20": -4.1, "freq": 2.9},
            ],
            "codes": ["SH600000", "SZ000001"],
            "current": {"enter_up": 70, "confirm_days": 2, "min_state_days": 3},
            "sample": {"mode": "market", "total": 2},
            "years": 1.2,
            "bars": 500,
            "cost": 0.00102,
            "limit_check": True,
            "plateau": {(65, 2, 1): {"n": 8, "mean": 0.9, "worst": -0.2, "positive": 75.0, "own": 0.8}},
        }
        saved = backtest_mod.save_result(result, self.root)
        self.assertTrue(saved.exists())

        payload = self.app.backtest()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["code_count"], 2)          # 代码列表不落盘，只留数量
        row = payload["items"][0]
        self.assertEqual(row["excess20"], 0.8)
        # 邻域信息要并进每一行，页面才不用自己拼元组 key
        self.assertEqual(row["plateau"]["worst"], -0.2)

    def test_saved_json_is_still_valid_python_free_json(self):
        """落盘的结果必须能被标准 json 读——元组 key 混进去过一次，页面直接读不出来。"""
        result = {"ok": True, "results": [{"params": {"enter_up": 70, "confirm_days": 2, "min_state_days": 3}}],
                  "codes": [], "plateau": {(70, 2, 3): {"mean": 0.5}}}
        saved = backtest_mod.save_result(result, self.root)
        data = json.loads(saved.read_text(encoding="utf-8"))
        self.assertIn("results", data)
        self.assertNotIn("plateau", data)
        self.assertEqual(data["results"][0]["plateau"]["mean"], 0.5)


if __name__ == "__main__":
    unittest.main()
