"""AI 指标分析（百炼）：只读旁注层，重点测三件事。

  1. 喂给模型的数字必须**来自本地算好的结果**，不能自己编、也不能夹带别家标的的数据；
  2. 没开、没 key、调用失败，都只影响这一次调用，绝不改任何本地结论；
  3. 每次调用都要原样留痕（提示词 + 回答 + token + 耗时），事后能回答"它当时看到了什么"。

测试全部离线：真正发 HTTP 的 analysis._post 会被替换掉。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from collector import analysis, db, server
from collector.config import load_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OK_RESPONSE = {
    "choices": [{"message": {"content": "① 震荡。② 支撑上方 2%。③ 仓位上限 14%。④ 样本偏少。"}}],
    "usage": {"prompt_tokens": 700, "completion_tokens": 60, "total_tokens": 760},
}


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=self.root)
        self.cfg["analysis"]["bailian"]["enabled"] = True
        self.cfg["_db_path"] = str(self.root / "t.db")
        self.conn = db.connect(self.cfg["_db_path"])
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")
        self.addCleanup(self.conn.close)
        self.key_file = self.root / "data" / "bailian.token"
        self.key_file.parent.mkdir(parents=True, exist_ok=True)
        self._env = mock.patch.dict(os.environ, {"DASHSCOPE_API_KEY": ""})
        self._env.start()
        self.addCleanup(self._env.stop)

    def _seed(self, code: str = "SH600487", trade_date: str = "2026-09-18"):
        db.upsert_rows(self.conn, "instruments",
                       [{"code": code, "name": "亨通光电", "type": "stock"}], ["code"])
        db.upsert_rows(self.conn, "bars_daily", [{
            "code": code, "trade_date": trade_date, "close": 69.49, "quality_flag": "ok",
        }], ["code", "trade_date"])
        db.upsert_rows(self.conn, "features_daily", [{
            "code": code, "trade_date": trade_date, "state": "range", "state_days": 14,
            "trend_score": 52.97, "opportunity_score": 0.4, "vol_ratio_20": 1.35, "atr_pct": 5.73,
            "position_cap": 0.14, "stop_level": 66.41, "risk_reward": 3.88,
            "risk_note": "range 基准仓位 40%", "data_quality_flag": "ok",
        }], ["code", "trade_date"])
        db.upsert_rows(self.conn, "levels", [{
            "code": code, "trade_date": trade_date, "level_type": "support",
            "price_low": 66.74, "price_high": 69.39, "weight": 1.0, "engine": "volume_profile",
        }], ["code", "trade_date", "level_type", "price_low", "price_high"])
        db.upsert_rows(self.conn, "factor_contributions", [
            {"code": code, "trade_date": trade_date, "factor_id": "ma_slope",
             "normalized_score": 1.0, "weight": 0.25, "contribution": 0.25},
            {"code": code, "trade_date": trade_date, "factor_id": "adx",
             "normalized_score": 0.5, "weight": 0.0, "contribution": 0.0},   # 影子因子，不该进提示词
        ], ["trade_date", "code", "factor_id"])
        db.upsert_rows(self.conn, "data_health", [
            {"run_date": trade_date, "source": "tencent", "task": "daily:SZ588000",
             "status": "retry", "error_msg": "别的标的的问题"},
            {"run_date": trade_date, "source": "tencent", "task": "breadth",
             "status": "suspect", "error_msg": "样本只有 7 只"},
        ], ["run_date", "source", "task"])

    def _write_key(self, text: str = "sk-test-key"):
        self.key_file.write_text(text + "\n", encoding="utf-8")

    # ---- key 从哪来 ----

    def test_key_priority_config_then_env_then_file(self):
        self._write_key("file-key")
        self.assertEqual(analysis.resolve_key(self.cfg)[0], "file-key")
        with mock.patch.dict(os.environ, {"DASHSCOPE_API_KEY": "env-key"}):
            self.assertEqual(analysis.resolve_key(self.cfg)[0], "env-key")
            self.cfg["analysis"]["bailian"]["token"] = "config-key"
            self.assertEqual(analysis.resolve_key(self.cfg)[0], "config-key")

    # ---- 喂进去的数字 ----

    def test_prompt_carries_the_numbers_and_nothing_else(self):
        self._seed()
        snap = analysis.snapshot(self.conn, self.cfg, "SH600487")
        text = analysis.render(snap)
        for expected in ("69.49", "震荡", "14", "52.9", "66.74", "3.88"):
            self.assertIn(expected, text)
        # 影子因子权重 0，不进提示词；别家标的的体检记录也不许混进来
        self.assertNotIn("adx", text)
        self.assertNotIn("SZ588000", text)
        self.assertIn("样本只有 7 只", text)          # 全局体检项要留着

    def test_prompt_forbids_advice_and_outside_data(self):
        self._seed()
        msgs = analysis.messages(analysis.snapshot(self.conn, self.cfg, "SH600487"))
        self.assertEqual(msgs[0]["role"], "system")
        self.assertIn("不许引入任何外部", msgs[0]["content"])
        self.assertIn("不许给目标价", msgs[0]["content"])

    def test_missing_features_is_a_readable_error(self):
        with self.assertRaises(analysis.AnalysisError):
            analysis.snapshot(self.conn, self.cfg, "SH600487")

    # ---- 调用与留痕 ----

    def test_disabled_means_no_call(self):
        self.cfg["analysis"]["bailian"]["enabled"] = False
        self._seed()
        result = analysis.analyze(self.conn, self.cfg, "SH600487")
        self.assertFalse(result["ok"])
        self.assertTrue(result["skipped"])
        self.assertIn("enabled", result["note"])

    def test_dry_run_prints_prompt_without_calling(self):
        self._seed()
        with mock.patch.object(analysis, "_post") as post:
            result = analysis.analyze(self.conn, self.cfg, "SH600487", dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertIn("亨通光电", result["prompt"])
        post.assert_not_called()
        self.assertEqual(db.query_one(self.conn, "SELECT COUNT(*) AS n FROM analysis_log")["n"], 0)

    def test_success_is_stored_with_usage(self):
        self._write_key()
        self._seed()
        with mock.patch.object(analysis, "_post", return_value=OK_RESPONSE) as post:
            result = analysis.analyze(self.conn, self.cfg, "SH600487")
        self.assertTrue(result["ok"], result.get("note"))
        self.assertIn("震荡", result["text"])
        self.assertEqual(result["usage"]["total_tokens"], 760)
        self.assertTrue(post.called)

        row = db.query_one(self.conn, "SELECT * FROM analysis_log WHERE code='SH600487'")
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["model"], "qwen-plus")
        self.assertEqual(row["prompt_tokens"], 700)
        self.assertIn("亨通光电", row["prompt"])        # 提示词原样留痕
        self.assertIn("震荡", row["answer"])
        self.assertEqual(analysis.latest(self.conn, "SH600487")["answer"], row["answer"])

    def test_twice_means_two_rows(self):
        self._write_key()
        self._seed()
        with mock.patch.object(analysis, "_post", return_value=OK_RESPONSE):
            analysis.analyze(self.conn, self.cfg, "SH600487")
            analysis.analyze(self.conn, self.cfg, "SH600487")
        self.assertEqual(db.query_one(self.conn, "SELECT COUNT(*) AS n FROM analysis_log")["n"], 2)

    def test_failure_is_recorded_but_does_not_raise(self):
        self._write_key()
        self._seed()
        with mock.patch.object(analysis, "_post", side_effect=analysis.AnalysisError("连不上百炼：超时")):
            result = analysis.analyze(self.conn, self.cfg, "SH600487")
        self.assertFalse(result["ok"])
        self.assertIn("超时", result["note"])
        row = db.query_one(self.conn, "SELECT * FROM analysis_log WHERE code='SH600487'")
        self.assertEqual(row["status"], "failed")
        self.assertIn("超时", row["error_msg"])
        self.assertIsNone(analysis.latest(self.conn, "SH600487"))    # 失败的不算"最近一次分析"

    def test_missing_key_says_where_to_put_it(self):
        self._seed()
        result = analysis.analyze(self.conn, self.cfg, "SH600487")
        self.assertFalse(result["ok"])
        self.assertIn("data/bailian.token", result["note"])

    def test_daily_limit_stops_runaway_use(self):
        """这东西按次计费，闸门必须在代码里，不能靠人自觉。"""
        self._write_key()
        self._seed()
        self.cfg["analysis"]["bailian"]["daily_limit"] = 2
        with mock.patch.object(analysis, "_post", return_value=OK_RESPONSE):
            self.assertTrue(analysis.analyze(self.conn, self.cfg, "SH600487")["ok"])
            self.assertTrue(analysis.analyze(self.conn, self.cfg, "SH600487")["ok"])
            with mock.patch.object(analysis, "_post") as post:
                blocked = analysis.analyze(self.conn, self.cfg, "SH600487")
            post.assert_not_called()
        self.assertTrue(blocked["skipped"])
        self.assertIn("上限", blocked["note"])

        # --force 是留给人明确表态的口子
        with mock.patch.object(analysis, "_post", return_value=OK_RESPONSE):
            forced = analysis.analyze(self.conn, self.cfg, "SH600487", force=True)
        self.assertTrue(forced["ok"])

    def test_dry_run_does_not_spend_quota(self):
        self.cfg["analysis"]["bailian"]["daily_limit"] = 1
        self._seed()
        for _ in range(3):
            self.assertTrue(analysis.analyze(self.conn, self.cfg, "SH600487", dry_run=True)["dry_run"])
        self.assertEqual(analysis.calls_today(self.conn), 0)

    # ---- 页面接口 ----

    def test_api_reports_switch_and_latest_answer(self):
        self._write_key()
        self._seed()
        app = server.App(self.cfg)
        empty = app.analysis("SH600487")
        self.assertTrue(empty["enabled"])
        self.assertTrue(empty["configured"])
        self.assertIsNone(empty["item"])

        with mock.patch.object(analysis, "_post", return_value=OK_RESPONSE):
            analysis.analyze(self.conn, self.cfg, "SH600487")
        payload = app.analysis("SH600487")
        self.assertEqual(payload["item"]["code"], "SH600487")
        self.assertEqual(payload["item"]["tokens"], 760)
        self.assertIn("震荡", payload["item"]["answer"])

    def test_api_without_code_is_not_an_exception(self):
        payload = server.App(self.cfg).analysis("")
        self.assertFalse(payload["ok"])

    # ---- 因子台账：喂汇总，不喂明细 ----

    def test_ledger_summary_is_small_and_complete(self):
        self._seed()
        # 塞一批明细进去：它们**不该**进提示词（几万行明细又贵又读不出东西）
        db.upsert_rows(self.conn, "factor_registry", [
            {"factor_id": "ma_slope", "name": "均线斜率", "layer": "state", "role": "primary",
             "category": "trend", "weight": 0.25, "status": "active"},
            {"factor_id": "adx", "name": "ADX", "layer": "state", "role": "modifier",
             "category": "trend", "weight": 0.0, "status": "shadow"},
        ], ["factor_id"])
        db.upsert_rows(self.conn, "factor_contributions", [
            {"code": "SH600487", "trade_date": "2026-09-18", "factor_id": "ma_slope",
             "normalized_score": 0.9, "weight": 0.25, "contribution": 0.22},
        ], ["trade_date", "code", "factor_id"])

        snap = analysis.ledger_snapshot(self.conn, self.cfg)
        text = analysis.render_ledger(snap)
        self.assertIn("影子运行 ≥ 20", text)
        self.assertIn("ma_slope", text)
        self.assertIn("adx", text)
        self.assertNotIn("SH600487", text)          # 明细里的标的不该出现
        self.assertLess(len(text), 2000)            # 汇总就该是这个体量

    def test_ledger_analysis_is_stored_under_its_own_scope(self):
        self._write_key()
        self._seed()
        with mock.patch.object(analysis, "_post", return_value=OK_RESPONSE):
            result = analysis.run(self.conn, self.cfg, analysis.SCOPE_LEDGER)
        self.assertTrue(result["ok"], result.get("note"))
        row = db.query_one(self.conn, "SELECT * FROM analysis_log WHERE scope='ledger'")
        self.assertIsNone(row["code"])
        self.assertEqual(analysis.latest(self.conn, None, scope=analysis.SCOPE_LEDGER)["answer"], row["answer"])
        # 和单只标的的记录互不干扰
        self.assertIsNone(analysis.latest(self.conn, "SH600487"))

    # ---- 回测：喂摘要，不喂整张表 ----

    def test_backtest_summary_needs_a_run_first(self):
        with self.assertRaises(analysis.AnalysisError):
            analysis.backtest_snapshot(self.cfg)

    def test_backtest_summary_keeps_the_ranking_that_matters(self):
        from collector import backtest as backtest_mod

        rows = []
        for index, enter in enumerate((70, 65, 60, 55)):
            rows.append({
                "label": f"enter_up={enter} 确认2日 最短3日",
                "params": {"enter_up": enter, "confirm_days": 2, "min_state_days": 3},
                "signals": 100 + index, "blocked": index, "win20": 50 - index,
                "avg20": 1.0 - index * 0.5, "base20": 0.6, "excess20": 0.4 - index * 0.5,
                "mdd20": -4.0, "freq": 3.0,
                "plateau": {"n": 6, "mean": 0.3, "worst": -0.2, "positive": 80.0},
            })
        backtest_mod.save_result(
            {"ok": True, "results": rows, "codes": ["a"] * 100, "bars": 1000,
             "current": {"enter_up": 65, "confirm_days": 2, "min_state_days": 3},
             "sample": {"mode": "market"}, "years": 2.0, "cost": 0.102, "limit_check": True},
            self.root,
        )
        snap = analysis.backtest_snapshot(self.cfg, top=2)
        self.assertEqual(snap["total"], 4)
        self.assertEqual(len(snap["top"]), 2)
        self.assertEqual(len(snap["bottom"]), 2)
        self.assertEqual(snap["current_rank"], 2)       # enter_up=65 排第二

        text = analysis.render_backtest(snap)
        self.assertIn("0.102%", text)                  # 成本是百分数，不能再乘 100
        self.assertIn("排第 2 / 4", text)
        self.assertIn("邻域", text)

    def test_backtest_analysis_records_scope(self):
        from collector import backtest as backtest_mod

        self._write_key()
        backtest_mod.save_result(
            {"ok": True, "results": [{"label": "x", "params": {"enter_up": 70, "confirm_days": 2, "min_state_days": 3},
                                      "excess20": 0.5, "signals": 10, "plateau": {}}],
             "codes": [], "current": {"enter_up": 70, "confirm_days": 2, "min_state_days": 3},
             "sample": {"mode": "market"}, "years": 1.0, "cost": 0.102, "limit_check": True},
            self.root,
        )
        with mock.patch.object(analysis, "_post", return_value=OK_RESPONSE):
            result = analysis.run(self.conn, self.cfg, analysis.SCOPE_BACKTEST)
        self.assertTrue(result["ok"], result.get("note"))
        self.assertEqual(analysis.latest(self.conn, None, scope=analysis.SCOPE_BACKTEST)["scope"], "backtest")

    def test_page_api_can_read_each_scope(self):
        self._write_key()
        self._seed()
        app = server.App(self.cfg)
        with mock.patch.object(analysis, "_post", return_value=OK_RESPONSE):
            analysis.run(self.conn, self.cfg, analysis.SCOPE_LEDGER)
        ledger = app.analysis("", analysis.SCOPE_LEDGER)
        self.assertEqual(ledger["scope"], "ledger")
        self.assertEqual(ledger["item"]["code"], "因子台账")
        self.assertIsNone(app.analysis("SH600487")["item"])


if __name__ == "__main__":
    unittest.main()
