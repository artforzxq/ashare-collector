"""摆动结构：更高的高点 + 更高的低点——趋势最原始的定义。

均线是平滑后的价格，结构是没被平滑过的原始序列。两条都留着，
才能回答"趋势分在涨，是结构真的在抬升，还是几根阳线把均值拉上去了"。

关键性质只有一条：**只认已经确认的拐点**。右边要等 SWING_SPAN 根才算数，
所以最近几个交易日永远不参与结构判定——不然就是在用未来数据画图。
"""

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from collector import features
from collector.config import load_config
from collector.registry import FactorRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _zigzag(pivots: list[float], leg: int = 6) -> list[float]:
    """把拐点连成一条锯齿序列（每段等分 leg 根）。"""
    series: list[float] = []
    for start, end in zip(pivots, pivots[1:]):
        series += [start + (end - start) * step / leg for step in range(leg)]
    series.append(pivots[-1])
    return series


def _bars(closes: list[float], turnover: float | None = None) -> list[dict]:
    start = date(2026, 1, 1)
    bars = []
    for index, close in enumerate(closes):
        bar = {
            "code": "SH600487",
            "trade_date": (start + timedelta(days=index)).isoformat(),
            "open": close * 0.998, "high": close * 1.005, "low": close * 0.995,
            "close": close, "volume": 1_000_000.0, "amount": close * 1_000_000,
            "pct_chg": 0.0, "quality_flag": "ok",
        }
        if turnover is not None:
            bar["turnover_rate"] = turnover
        bars.append(bar)
    return bars


class SwingStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=Path(cls.tmp.name))
        cls.cfg["watchlist"] = {"indices": [], "etfs": [], "stocks": ["SH600487"]}
        cls.registry = FactorRegistry(cls.cfg.get("factors", []), "test")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _compute(self, closes):
        return features.compute_feature_series(_bars(closes), self.cfg, self.registry, {})

    def test_rising_highs_and_lows_is_up_structure(self):
        rows = self._compute(_zigzag([10.0, 12.0, 11.0, 13.0, 12.5, 14.0]))
        self.assertEqual(rows[-1]["swing_state"], 1.0)

    def test_falling_highs_and_lows_is_down_structure(self):
        rows = self._compute(_zigzag([14.0, 12.0, 13.0, 11.0, 11.5, 10.0]))
        self.assertEqual(rows[-1]["swing_state"], -1.0)

    def test_mixed_swings_are_neither(self):
        rows = self._compute(_zigzag([10.0, 13.0, 11.5, 14.0, 11.0, 12.0]))
        self.assertEqual(rows[-1]["swing_state"], 0.0)

    def test_short_history_has_no_structure_conclusion(self):
        """连两个拐点都凑不齐就明说没有结论，别硬给一个数。"""
        rows = self._compute([10.0] * 4)
        self.assertIsNone(rows[-1]["swing_state"])
        self.assertIsNone(rows[-1]["swing_low_1"])

    def test_flat_market_has_no_direction(self):
        """一条直线上的"拐点"谁都不比谁高——结论只能是"结构没方向"，不能算多头。"""
        rows = self._compute([10.0] * 20)
        self.assertEqual(rows[-1]["swing_state"], 0.0)

    def test_recent_bars_never_enter_the_judgement(self):
        """最近 SWING_SPAN 根不参与：它们右边的行情还没发生，用来判结构就是未来函数。"""
        rows = self._compute(_zigzag([10.0, 12.0, 11.0, 13.0]))
        for row in rows[-features.SWING_SPAN:]:
            if row["swing_low_1"] is None:
                continue
            self.assertGreaterEqual(row["bars_since_swing_low"], features.SWING_SPAN)

    def test_distance_to_the_structural_low_is_measured_from_the_close(self):
        rows = self._compute(_zigzag([10.0, 12.0, 11.0, 13.0]))
        last = rows[-1]
        expected = (last["close"] - last["swing_low_1"]) / last["close"] * 100
        self.assertAlmostEqual(last["dist_to_swing_low"], expected, places=3)

    def test_both_structure_shadow_factors_are_recorded_with_zero_weight(self):
        rows = self._compute(_zigzag([10.0, 12.0, 11.0, 13.0, 12.5, 14.0]))
        contributions = {item["factor_id"]: item for item in rows[-1]["contributions"]}
        for factor_id in ("swing_state", "dist_to_swing_low"):
            self.assertIn(factor_id, contributions)
            self.assertEqual(contributions[factor_id]["weight"], 0.0)


if __name__ == "__main__":
    unittest.main()
