"""环境体检：Python / 依赖 / 数据库 / 配置。

它只读——不建库、不装包、改不了配置。所以这里也顺带盯一条：**绝不把 token 打出来**。
"""

import tempfile
import unittest
from unittest import mock
from pathlib import Path

from collector import db, doctor
from collector.config import load_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=self.root)
        self.cfg["_db_path"] = str(self.root / "data" / "market.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_database_says_how_to_create_it(self):
        report = doctor.check_database(self.cfg["_db_path"], PROJECT_ROOT / "schema.sql")
        self.assertFalse(report["exists"])
        self.assertFalse(report["ok"])
        self.assertIn("init-db", report["note"])

    def test_empty_database_is_created_but_not_ready(self):
        """库建出来了、表也齐，但没有数据——这不是"坏"，是"还没跑任务"。"""
        conn = db.connect(self.cfg["_db_path"])
        db.init_db(conn, PROJECT_ROOT / "schema.sql")
        conn.close()
        report = doctor.check_database(self.cfg["_db_path"], PROJECT_ROOT / "schema.sql")
        self.assertTrue(report["exists"])
        self.assertEqual(report["integrity"], "ok")
        self.assertEqual(report["missing_tables"], [])
        self.assertFalse(report["ok"])
        self.assertIn("日线表是空的", report["note"])

    def test_database_with_data_is_healthy(self):
        conn = db.connect(self.cfg["_db_path"])
        db.init_db(conn, PROJECT_ROOT / "schema.sql")
        db.upsert_rows(conn, "bars_daily", [{
            "code": "SH600000", "trade_date": "2026-09-18", "close": 10.0,
        }], ["code", "trade_date"])
        conn.close()
        report = doctor.check_database(self.cfg["_db_path"], PROJECT_ROOT / "schema.sql")
        self.assertTrue(report["ok"])
        self.assertEqual(report["latest_bar"], "2026-09-18")
        self.assertEqual(report["rows"]["bars_daily"], 1)

    def test_report_covers_the_four_parts(self):
        result = doctor.report(self.cfg, db_path=self.cfg["_db_path"])
        for key in ("python", "modules", "database", "config"):
            self.assertIn(key, result)
        text = doctor.render(result)
        for label in ("Python", "必需依赖", "数据源适配器", "数据库", "配置"):
            self.assertIn(label, text)

    def test_sources_check_lists_every_registry_adapter(self):
        """装没装（本地 import）永远查；通不通要显式 --sources 才联网。"""
        from collector.sources import SOURCE_REGISTRY

        result = doctor.check_sources(self.cfg, probe=False)
        names = {item["name"] for item in result["items"]}
        self.assertNotIn("fixture", names)          # 夹具源不是真数据源
        self.assertIn("tencent", names)
        self.assertEqual(names, set(SOURCE_REGISTRY) - {"fixture", "fixture_alt", "csv"})
        self.assertFalse(result["probed"])          # 默认不联网
        self.assertEqual(result["configured"]["primary"], "tencent")

    def test_sources_probe_reports_the_real_verdict(self):
        """探测要真的取数：通的说通，断的说断，别都报"装了"。"""
        calls = []

        class FakeSource:
            capabilities = {"daily_bars"}

            def __init__(self, name, ok):
                self.name = name
                self._ok = ok

            def is_available(self):
                return True, "已安装"

            def daily_bars(self, code, start, end, kind="stock"):
                calls.append(self.name)
                if not self._ok:
                    raise RuntimeError("接口被断连")
                return [{"trade_date": "2026-09-18", "close": 1.0}]

        def fake_build(name, cfg):
            return FakeSource(name, ok=(name != "akshare"))

        with mock.patch("collector.sources.build_source", fake_build):
            items = doctor.probe_sources(self.cfg)
        by_name = {item["name"]: item for item in items}
        self.assertTrue(by_name["tencent"]["ok"])
        self.assertEqual(by_name["tencent"]["bars"], 1)
        self.assertFalse(by_name["akshare"]["ok"])
        self.assertIn("断连", by_name["akshare"]["note"])

    def test_render_shows_the_source_verdict_and_the_fallback_warning(self):
        result = doctor.report(self.cfg, db_path=self.cfg["_db_path"])
        result["sources"] = {
            "probed": True, "usable": ["baostock"], "configured": {"primary": "tencent"},
            "items": [{"name": "tencent", "installed": True, "ok": False, "note": "连不上"},
                      {"name": "baostock", "installed": True, "ok": True, "note": "真取到 30 根日线"}],
        }
        text = doctor.render(result)
        self.assertIn("数据源可用性", text)
        self.assertIn("[✓] baostock", text)
        self.assertIn("[✗] tencent", text)
        self.assertIn("主源 tencent 现在不能用", text)

    def test_capability_matrix_names_what_breaks_when_a_source_dies(self):
        """体检不该只说"akshare 挂了"，还要说"因此哪件事做不了"。"""
        items = [
            {"name": "tencent", "installed": True, "ok": True, "note": "", "bars": 0,
             "capabilities": ["daily_bars", "market_snapshot"]},
            {"name": "akshare", "installed": True, "ok": False, "note": "连不上", "bars": 0,
             "capabilities": ["daily_bars", "etf_shares", "lhb", "margin"]},
        ]
        usable = [i["name"] for i in items if i["installed"] and i["ok"] is not False]
        coverage: dict = {}
        for item in items:
            if item["name"] in usable:
                for capability in item["capabilities"]:
                    coverage.setdefault(capability, []).append(item["name"])
        gaps = {cap: doctor.CAPABILITY_USES.get(cap, "")
                for cap in doctor.CAPABILITY_USES if cap not in coverage}
        # etf_shares / lhb / margin 只剩 akshare 提供，它一挂这几个能力就断了
        self.assertIn("etf_shares", gaps)
        self.assertIn("影子因子", gaps["etf_shares"])
        self.assertIn("龙虎榜", gaps["lhb"])
        self.assertNotIn("daily_bars", gaps)          # 腾讯还顶着，日线不受影响

    def test_render_lists_the_gaps(self):
        result = doctor.report(self.cfg, db_path=self.cfg["_db_path"])
        result["sources"] = {
            "probed": True, "usable": ["tencent"], "configured": {"primary": "tencent"},
            "coverage": {"daily_bars": ["tencent"]},
            "gaps": {"etf_shares": doctor.CAPABILITY_USES["etf_shares"]},
            "items": [{"name": "tencent", "installed": True, "ok": True, "note": "真取到 30 根日线"}],
        }
        text = doctor.render(result)
        self.assertIn("没有可用源的能力", text)
        self.assertIn("etf_shares", text)

    def test_tokens_are_never_printed(self):
        """体检报告会被贴到聊天里、贴进 issue——真 token 绝不能出现在里面。"""
        (self.root / "data").mkdir(parents=True, exist_ok=True)
        (self.root / "data" / "pushplus.token").write_text("SECRET-PUSHPLUS-TOKEN\n", encoding="utf-8")
        (self.root / "data" / "bailian.token").write_text("SECRET-BAILIAN-KEY\n", encoding="utf-8")
        text = doctor.render(doctor.report(self.cfg, db_path=self.cfg["_db_path"]))
        self.assertNotIn("SECRET-PUSHPLUS-TOKEN", text)
        self.assertNotIn("SECRET-BAILIAN-KEY", text)
        self.assertIn("token 已配置", text)
        self.assertIn("key 已配置", text)


if __name__ == "__main__":
    unittest.main()
