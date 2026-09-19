"""换手率特征：占比、常态、放量倍数，以及缺值怎么处理。

换手率只有 baostock 给（腾讯、新浪都不给），本地大约四分之一的行是空的。
所以这一层的重点不是"算得准不准"，而是**缺数据时别假装有结论**：
窗口有效值不够就置空，并把覆盖率记下来，让体检能看出这个因子靠不靠谱。
"""

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from collector import db, features
from collector.config import load_config
from collector.registry import FactorRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _bars(turnovers, close: float = 10.0) -> list[dict]:
    start = date(2026, 1, 5)
    bars = []
    for index, turnover in enumerate(turnovers):
        bars.append({
            "code": "SH600487",
            "trade_date": (start + timedelta(days=index)).isoformat(),
            "open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
            "pre_close": close, "pct_chg": 0.0, "volume": 1_000_000.0,
            "amount": 1e8, "quality_flag": "ok",
            "turnover_rate": turnover,
        })
    return bars


class TurnoverFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=Path(cls.tmp.name))
        cls.cfg["watchlist"] = {"indices": [], "etfs": [], "stocks": ["SH600487"]}
        cls.registry = FactorRegistry(cls.cfg.get("factors", []), "test")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _compute(self, turnovers):
        return features.compute_feature_series(_bars(turnovers), self.cfg, self.registry, {})

    def test_ratio_is_today_over_its_own_norm(self):
        # 前 25 天换手 4%，然后放量到 12%（3 倍）
        rows = self._compute([4.0] * 25 + [12.0])
        last = rows[-1]
        self.assertEqual(last["turnover_rate"], 12.0)
        self.assertAlmostEqual(last["turnover_20d"], 4.0, places=3)
        self.assertAlmostEqual(last["turnover_ratio"], 3.0, places=3)
        self.assertEqual(last["turnover_coverage"], 1.0)

    def test_window_excludes_today(self):
        """窗口不含当日——这是全文件统一口径，否则"放量倍数"会被自己算进去。"""
        calm = self._compute([4.0] * 25 + [4.0])
        spike = self._compute([4.0] * 25 + [40.0])
        self.assertEqual(calm[-1]["turnover_20d"], spike[-1]["turnover_20d"])

    def test_missing_values_are_tolerated_but_counted(self):
        """缺两三天不该让整个窗口作废，但覆盖率要如实记下来。"""
        turnovers = [4.0] * 20 + [None, 4.0, None, 8.0]
        # 最后一天的 20 日窗口 = 它之前的 20 根：18 根有值 → 覆盖率 0.9
        last = self._compute(turnovers)[-1]
        self.assertIsNotNone(last["turnover_20d"])
        self.assertEqual(last["turnover_coverage"], 0.9)
        self.assertAlmostEqual(last["turnover_ratio"], 8.0 / 4.0, places=3)

    def test_too_many_missing_values_gives_no_conclusion(self):
        """有效值不到六成就置空——宁可没有结论，也别拿三四天的均值当"常态"。"""
        # 最后一天的窗口里只有 10 根有值 → 覆盖率 0.5，低于 0.6 的门槛
        turnovers = [None] * 15 + [4.0] * 10 + [12.0]
        last = self._compute(turnovers)[-1]
        self.assertIsNone(last["turnover_20d"])
        self.assertIsNone(last["turnover_ratio"])
        self.assertLess(last["turnover_coverage"], 0.6)

    def test_no_turnover_data_at_all_is_silent(self):
        rows = self._compute([None] * 30)
        last = rows[-1]
        self.assertIsNone(last["turnover_rate"])
        self.assertIsNone(last["turnover_20d"])
        self.assertIsNone(last["turnover_ratio"])
        self.assertEqual(last["turnover_coverage"], 0.0)

    def test_both_shadow_factors_are_recorded_with_zero_weight(self):
        """影子因子：记录贡献分，但不参与打分（权重 0）。"""
        rows = self._compute([4.0] * 25 + [12.0])
        last = rows[-1]
        contributions = {item["factor_id"]: item for item in last["contributions"]}
        self.assertIn("turnover_ratio", contributions)
        self.assertIn("turnover_20d", contributions)
        self.assertEqual(contributions["turnover_ratio"]["weight"], 0.0)
        self.assertEqual(contributions["turnover_20d"]["weight"], 0.0)
        # 归一化分要看得到（体检算 IC 就用它），否则影子运行等于白跑
        self.assertIsNotNone(contributions["turnover_ratio"]["normalized_score"])

    def test_bars_without_the_column_do_not_break_anything(self):
        """老数据（没有 turnover_rate 列的历史行）也要能算完。"""
        bars = _bars([4.0] * 25)
        for bar in bars:
            bar.pop("turnover_rate")
        rows = features.compute_feature_series(bars, self.cfg, self.registry, {})
        self.assertIsNone(rows[-1]["turnover_ratio"])


if __name__ == "__main__":
    unittest.main()
