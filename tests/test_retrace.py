"""斐波那契回撤：口径必须严格——它和"区间位置"是两个东西，特别容易混。"""

import unittest

from collector import features, risk
from collector.registry import FactorRegistry


class RetraceTests(unittest.TestCase):
    def test_textbook_case(self):
        """教科书那个例子：10 元涨到 20 元，回撤到 16.18 元 = 回撤 38.2%。

        低点 (bars_since_low) 比高点 (bars_since_high) 更早，才是"先涨后回"。
        """
        value = features._retrace(close=16.18, high=20.0, low=10.0,
                                  bars_since_low=30, bars_since_high=10)
        self.assertAlmostEqual(value, 0.382, places=3)
        # 贴着高点 → 0；正好跌回起点 → 算"结构破了"，不给数（不是 1.0）
        self.assertAlmostEqual(features._retrace(19.99, 20.0, 10.0, 30, 10), 0.001, places=3)
        self.assertIsNone(features._retrace(10.0, 20.0, 10.0, 30, 10))

    def test_requires_low_before_high(self):
        """低点比高点更新 = 这段是"下跌之后的反抽"，不是"上涨之后的回撤" → 不给数。"""
        self.assertIsNone(features._retrace(16.0, 20.0, 10.0, bars_since_low=5,
                                            bars_since_high=30))

    def test_requires_a_real_leg(self):
        """涨幅不到 10% 不算"一段上涨"：微涨 2% 的"回撤 61.8%"没有意义。"""
        self.assertIsNone(features._retrace(10.15, 10.2, 10.0, 30, 10))
        self.assertIsNotNone(features._retrace(10.8, 11.2, 10.0, 30, 10))

    def test_out_of_range_prices_are_rejected(self):
        self.assertIsNone(features._retrace(20.5, 20.0, 10.0, 30, 10))   # 已经创新高
        self.assertIsNone(features._retrace(9.5, 20.0, 10.0, 30, 10))    # 已跌破起点

    def test_feature_series_carries_it(self):
        bars = []
        for index in range(60):
            # 先涨后回：前 40 根从 10 升到 20，后 20 根回到 16
            close = 10 + index * 0.25 if index < 40 else 20 - (index - 40) * 0.2
            bars.append({"code": "SH600000", "trade_date": f"2026-01-{index + 1:02d}", "open": close,
                         "high": close * 1.01, "low": close * 0.99, "close": close,
                         "volume": 1000, "amount": 1e8})
        series = features.compute_feature_series(bars, {}, FactorRegistry([]), {})
        self.assertIn("retrace", series[-1])


class FibTargetTests(unittest.TestCase):
    """斐波那契扩展目标：只收紧、不放宽。"""

    def _row(self, **over):
        row = {"swing_state": 1, "swing_low_1": 10.0, "swing_high_1": 20.0,
               "bars_since_swing_low": 30, "bars_since_swing_high": 10}
        row.update(over)
        return row

    def test_target_is_low_plus_multiple_of_the_leg(self):
        self.assertAlmostEqual(risk.fib_extension_target(self._row(), 1.618),
                               10 + 1.618 * 10, places=4)

    def test_needs_an_uptrend_structure(self):
        self.assertIsNone(risk.fib_extension_target(self._row(swing_state=-1), 1.618))
        self.assertIsNone(risk.fib_extension_target(self._row(swing_state=0), 1.618))

    def test_needs_low_before_high(self):
        self.assertIsNone(risk.fib_extension_target(
            self._row(bars_since_swing_low=5, bars_since_swing_high=30), 1.618))

    def test_missing_swing_data_gives_nothing(self):
        self.assertIsNone(risk.fib_extension_target({}, 1.618))
        self.assertIsNone(risk.fib_extension_target(
            self._row(swing_low_1=None, swing_high_1=None), 1.618))


if __name__ == "__main__":
    unittest.main()
