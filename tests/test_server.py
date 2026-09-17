"""看盘页面的路由与自选维护测试（不联网、不开端口）。"""

import json

import tempfile
import unittest
from pathlib import Path

from collector import db, server, tasks
from collector.config import load_config, use_fixture_sources

PROJECT_ROOT = Path(__file__).resolve().parents[1]

CONFIG_SAMPLE = """\
project:
  name: ashare-collector
  db_path: data/market.db

# 观察池注释必须保留
watchlist:
  indices: [SH000300]
  etfs: []
  stocks: []
"""


def _seed_db(path: Path) -> None:
    conn = db.connect(path)
    db.init_db(conn, PROJECT_ROOT / "schema.sql")
    db.upsert_rows(
        conn,
        "bars_daily",
        [
            {"code": "SH000300", "trade_date": "2026-09-15", "open": 4400.0, "high": 4460.0,
             "low": 4390.0, "close": 4450.0, "volume": 1e8, "amount": 4e11, "pct_chg": -0.67},
            {"code": "SH000300", "trade_date": "2026-09-16", "open": 4449.0, "high": 4483.0,
             "low": 4417.0, "close": 4480.0, "volume": 1.2e8, "amount": 4.7e11, "pct_chg": 0.68},
        ],
        ["code", "trade_date"],
    )
    db.upsert_rows(
        conn,
        "features_daily",
        [
            {"code": "SH000300", "trade_date": "2026-09-16", "ma20": 4400.0, "ma60": 4300.0,
             "ma120": 4200.0, "trend_score": 38.2, "state": "range", "state_days": 5,
             "opportunity_score": 0.5, "vol_ratio_20": 1.1},
        ],
        ["code", "trade_date"],
    )
    db.upsert_rows(
        conn,
        "levels",
        [{"code": "SH000300", "trade_date": "2026-09-16", "level_type": "resistance",
          "price_low": 4568.0, "price_high": 4590.0, "weight": 1.0, "engine": "volume_profile"}],
        ["code", "trade_date", "level_type", "price_low", "price_high"],
    )
    db.upsert_rows(
        conn,
        "alerts",
        [{"created_at": "2026-09-16 20:00:00", "trade_date": "2026-09-16", "code": "SH000300",
          "level": "P1", "signal_type": "STATE_TO_UP", "message": "状态转上升趋势"}],
        ["code", "trade_date", "signal_type", "level"],
    )
    db.upsert_rows(
        conn,
        "factor_registry",
        [{"factor_id": "ma_slope", "name": "均线斜率", "layer": "state", "role": "primary",
          "category": "trend", "weight": 0.25, "status": "active", "feature_version": "v1"}],
        ["factor_id"],
    )
    db.upsert_rows(
        conn,
        "factor_contributions",
        [{"trade_date": "2026-09-16", "code": "SH000300", "factor_id": "ma_slope",
          "raw_value": -0.5, "normalized_score": 0.4, "weight": 0.25, "contribution": 0.1,
          "feature_version": "v1"}],
        ["trade_date", "code", "factor_id"],
    )
    conn.close()


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.cfg_path = root / "config.yaml"
        cls.cfg_path.write_text(CONFIG_SAMPLE, encoding="utf-8")
        cls.cfg = load_config(cls.cfg_path, project_root=root)
        cls.cfg["_db_path"] = str(root / "market.db")
        cls.cfg["_config_path"] = str(cls.cfg_path)
        _seed_db(Path(cls.cfg["_db_path"]))
        cls.app = server.App(cls.cfg)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    # ---- 代码规整 ----

    def test_normalize_code(self):
        self.assertEqual(server.normalize_code("600519"), "SH600519")
        self.assertEqual(server.normalize_code("sh600519"), "SH600519")
        self.assertEqual(server.normalize_code("600519.SH"), "SH600519")
        self.assertEqual(server.normalize_code("000001"), "SZ000001")
        self.assertEqual(server.normalize_code("510300"), "SH510300")
        self.assertIsNone(server.normalize_code("随便写的"))

    def test_guess_kind(self):
        self.assertEqual(server.guess_kind("SH000300"), "index")
        self.assertEqual(server.guess_kind("SZ399006"), "index")
        self.assertEqual(server.guess_kind("SH510300"), "etf")
        self.assertEqual(server.guess_kind("SZ159919"), "etf")
        self.assertEqual(server.guess_kind("SH600519"), "stock")

    # ---- 路由 ----

    def test_index_page_is_served(self):
        status, ctype, body = server.dispatch(self.app, "GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"<canvas", body)

    def test_watchlist_api(self):
        status, _, body = server.dispatch(self.app, "GET", "/api/watchlist")
        items = json.loads(body)["items"]
        self.assertEqual(status, 200)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["code"], "SH000300")
        self.assertEqual(items[0]["state"], "range")
        self.assertEqual(items[0]["bars"], 2)

    def test_kline_api_joins_features_levels_alerts(self):
        status, _, body = server.dispatch(self.app, "GET", "/api/kline?code=SH000300&days=60")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["bars"]), 2)
        self.assertEqual(payload["bars"][-1]["ma20"], 4400.0)
        self.assertEqual(payload["levels"][0]["level_type"], "resistance")
        self.assertEqual(payload["alerts"][0]["level"], "P1")
        self.assertEqual(payload["factors"][0]["name"], "均线斜率")

    def test_unknown_route_returns_404(self):
        status, _, body = server.dispatch(self.app, "GET", "/api/nope")
        self.assertEqual(status, 404)
        self.assertIn("error", json.loads(body))

    # ---- 自选维护 ----

    def test_remove_keeps_config_comments(self):
        # 用一个独立的配置副本，免得把自选改空了影响别的用例
        cfg_path = Path(self.tmp.name) / "config-remove.yaml"
        cfg_path.write_text(CONFIG_SAMPLE, encoding="utf-8")
        cfg = load_config(cfg_path, project_root=Path(self.tmp.name))
        cfg["_db_path"] = self.cfg["_db_path"]
        cfg["_config_path"] = str(cfg_path)
        app = server.App(cfg)
        result = server.dispatch(
            app, "POST", "/api/watchlist", json.dumps({"action": "remove", "code": "SH000300"}).encode()
        )
        self.assertTrue(json.loads(result[2])["ok"])
        text = cfg_path.read_text(encoding="utf-8")
        self.assertIn("# 观察池注释必须保留", text)
        self.assertIn("indices: []", text)
        self.assertEqual(cfg["watchlist"]["indices"], [])

    def test_rewrite_watchlist_preserves_other_lines(self):
        cfg_path = Path(self.tmp.name) / "config2.yaml"
        cfg_path.write_text(CONFIG_SAMPLE, encoding="utf-8")
        cfg = load_config(cfg_path, project_root=Path(self.tmp.name))
        cfg["watchlist"] = {"indices": ["SH000300", "SH000905"], "etfs": ["SH510300"], "stocks": []}
        server._rewrite_watchlist(cfg_path, cfg)
        text = cfg_path.read_text(encoding="utf-8")
        self.assertIn("indices: [SH000300, SH000905]", text)
        self.assertIn("etfs: [SH510300]", text)
        self.assertIn("# 观察池注释必须保留", text)
        self.assertIn("name: ashare-collector", text)

    def test_add_rejects_unknown_code(self):
        result = server.dispatch(self.app, "POST", "/api/watchlist", json.dumps({"action": "add", "code": "??"}).encode())
        payload = json.loads(result[2])
        self.assertFalse(payload["ok"])


class CollectInstrumentTests(unittest.TestCase):
    """页面里加自选 = 给单只标的补数据。这条链路用离线夹具验证。"""

    def test_new_instrument_gets_bars_features_and_levels(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=PROJECT_ROOT)
            use_fixture_sources(cfg, end_date="2026-09-16")     # 不联网
            cfg["_db_path"] = str(Path(tmp) / "t.db")
            cfg["_config_path"] = str(PROJECT_ROOT / "config.yaml")
            cfg["watchlist"]["stocks"] = ["SH600000"]
            conn = db.connect(cfg["_db_path"])
            db.init_db(conn, PROJECT_ROOT / "schema.sql")
            try:
                result = tasks.collect_instrument(conn, cfg, "SH600000", "stock")
                self.assertTrue(result["ok"], result.get("message"))
                self.assertGreater(result["days"], 200)
                bars = db.query_one(conn, "SELECT COUNT(*) AS n FROM bars_daily WHERE code='SH600000'")["n"]
                feats = db.query_one(conn, "SELECT COUNT(*) AS n FROM features_daily WHERE code='SH600000'")["n"]
                levels = db.query_one(conn, "SELECT COUNT(*) AS n FROM levels WHERE code='SH600000'")["n"]
                self.assertGreater(bars, 200)
                self.assertGreater(feats, 100)
                self.assertGreater(levels, 0)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
