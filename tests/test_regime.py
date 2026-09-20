"""市场层（Beta）：合成口径、资产选择规则、以及"缺输入就不下结论"。

它是那份"Alpha/Beta 分离"材料里唯一站得住的部分：Beta 回答"今天该用多大仓位、
该用哪类工具"，Alpha 回答"买哪只票"。两件事的证据强度不一样，所以这里只做前者。

关键纪律（都在测试里钉住）：
  · 合成至少要两块输入——只有广度、或者只有指数状态时**不下结论**（不拿 0.5 冒充）；
  · 市场层没有数据时，资产选择按中性排序，但 `available=False`，不许假装"系统判断是中性"；
  · 资产选择只改排序与提示，不改筛选口径。
"""

import tempfile
import unittest
from pathlib import Path

from collector import db, regime
from collector.config import load_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _bar(code: str, day: str, close: float) -> dict:
    return {"code": code, "trade_date": day, "open": close, "high": close * 1.01,
            "low": close * 0.99, "close": close, "volume": 1e6, "amount": close * 1e6,
            "pct_chg": 0.0, "quality_flag": "ok"}


class StanceTests(unittest.TestCase):
    def test_thresholds_are_readable(self):
        self.assertEqual(regime.stance(0.2), "防守")
        self.assertEqual(regime.stance(0.5), "中性")
        self.assertEqual(regime.stance(0.8), "进取")
        self.assertEqual(regime.stance(None), "无")


class KindTests(unittest.TestCase):
    CFG = {"market": {"broad_etfs": ["SH510300", "SZ159919"]}}

    def test_kinds(self):
        self.assertEqual(regime.kind_of(self.CFG, "SH510300", "etf"), "broad_etf")
        self.assertEqual(regime.kind_of(self.CFG, "SH513100", "etf"), "sector_etf")
        self.assertEqual(regime.kind_of(self.CFG, "SZ000333", "stock"), "stock")
        self.assertEqual(regime.kind_of(self.CFG, "SH000300", "index"), "index")

    def test_unknown_type_falls_back_safely(self):
        """类型缺失时不能瞎归类：只有明确在宽基名单里才算宽基。"""
        self.assertEqual(regime.kind_of(self.CFG, "SH510300", None), "broad_etf")
        self.assertEqual(regime.kind_of(self.CFG, "SH999999", None), "other")


class AssetMixTests(unittest.TestCase):
    CFG = {"market": {}}

    def test_defense_leans_to_broad_etf(self):
        mix = regime.asset_mix(self.CFG, 0.25)
        self.assertEqual(mix["stance"], "防守")
        self.assertEqual(mix["preferred"], "broad_etf")
        self.assertGreater(mix["weights"]["broad_etf"], mix["weights"]["stock"])

    def test_offense_leans_to_stocks(self):
        mix = regime.asset_mix(self.CFG, 0.8)
        self.assertEqual(mix["preferred"], "stock")
        self.assertGreater(mix["weights"]["stock"], mix["weights"]["broad_etf"])

    def test_missing_score_is_neutral_but_flagged(self):
        mix = regime.asset_mix(self.CFG, None)
        self.assertFalse(mix["available"])
        self.assertIn("无数据", mix["stance"])
        self.assertEqual(mix["key"], "neutral")

    def test_config_can_override_the_mix(self):
        cfg = {"market": {"asset_mix": {"defense": {"broad_etf": 0.9, "sector_etf": 0.05, "stock": 0.05}}}}
        mix = regime.asset_mix(cfg, 0.1)
        self.assertAlmostEqual(mix["weights"]["broad_etf"], 0.9)


class SeriesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=Path(self.tmp.name))
        self.cfg["_db_path"] = str(Path(self.tmp.name) / "t.db")
        self.cfg["market"] = {"indices": ["SH000300"]}
        self.conn = db.connect(self.cfg["_db_path"])
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _seed(self, days: int = 90, rising: bool = True):
        rows = []
        for index in range(days):
            day = f"2026-{(index // 28) + 1:02d}-{(index % 28) + 1:02d}"
            close = 100 + (index if rising else -index)
            rows.append(_bar("SH000300", day, float(close)))
        db.upsert_rows(self.conn, "bars_daily", rows, ["code", "trade_date"])
        breadth = []
        for index in range(days):
            day = f"2026-{(index // 28) + 1:02d}-{(index % 28) + 1:02d}"
            breadth.append({"trade_date": day, "up_ratio": 0.8 if rising else 0.2,
                            "total_amount": 1e12 + index * 1e9})
        db.upsert_rows(self.conn, "market_breadth", breadth, ["trade_date"])
        self.conn.commit()

    def test_rising_market_scores_higher_than_falling(self):
        self._seed(rising=True)
        up = regime.series(self.conn, self.cfg)
        # 换一份干净数据再算"下跌市"之前，必须先关掉连接：Windows 上删不掉
        # 还被进程占着的临时库（WinError 32），macOS 允许，所以这个坑只在 Windows 上露头。
        self.conn.close()
        self.tmp.cleanup()
        self.setUp()
        self._seed(rising=False)
        down = regime.series(self.conn, self.cfg)
        self.assertTrue(up and down)
        self.assertGreater(max(up.values()), max(down.values()))

    def test_scores_stay_within_zero_and_one(self):
        self._seed()
        values = regime.series(self.conn, self.cfg)
        self.assertTrue(values)
        for value in values.values():
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)

    def test_one_input_is_not_enough_for_a_conclusion(self):
        """只有广度、没有指数状态（或反过来）时不下结论——0.5 是"我不知道"，不是"中性"。"""
        self._seed()
        self.conn.execute("DELETE FROM bars_daily")
        self.conn.commit()
        self.assertEqual(regime.series(self.conn, self.cfg), {})

    def test_latest_carries_the_reading_and_history(self):
        self._seed()
        info = regime.latest(self.conn, self.cfg)
        self.assertTrue(info["ok"])
        self.assertIn(info["stance"], ("防守", "中性", "进取"))
        self.assertTrue(info["history"])

    def test_latest_says_so_when_there_is_nothing(self):
        info = regime.latest(self.conn, self.cfg)
        self.assertFalse(info["ok"])
        self.assertIn("还没有", info["message"])


if __name__ == "__main__":
    unittest.main()
