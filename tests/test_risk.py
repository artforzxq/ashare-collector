"""风险层规则测试：仓位上限与止损位的边界行为。"""

import unittest

from collector import risk


class RiskAssessTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {"risk": {}}
        self.bands = [
            {"level_type": "support", "price_low": 95.0, "price_high": 97.0, "weight": 1.0},
            {"level_type": "resistance", "price_low": 110.0, "price_high": 112.0, "weight": 1.0},
        ]
        self.row = {"close": 100.0, "state": "up", "atr_pct": 2.0, "atr14": 2.0, "quality_flag": "ok"}

    def test_uptrend_gets_cap_and_stop_under_support(self):
        result = risk.assess(self.row, self.bands, self.cfg)
        self.assertGreater(result["position_cap"], 0)
        self.assertLess(result["stop_level"], 95.0)          # 止损在支撑带下沿再留一点
        self.assertAlmostEqual(result["stop_level"], 95.0 * 0.995, places=3)
        self.assertIn("支撑带下沿", result["reason"])

    def test_downtrend_without_reversal_is_flat(self):
        """下跌趋势里默认不建仓——除非 K 线给出反转确认（见下一个用例）。"""
        result = risk.assess({**self.row, "state": "down"}, self.bands, self.cfg)
        self.assertEqual(result["position_cap"], 0)
        self.assertIsNone(result["stop_level"])          # 0 仓不报止损位，免得看着矛盾
        self.assertIn("没有反转确认", result["reason"])

    def test_downtrend_with_reversal_gets_probe_position(self):
        """下跌趋势 + 放量长阳 → 允许试探仓，止损放平台下沿。"""
        candle = {"reversal_up": True, "long_bull": True, "pattern": "放量长阳"}
        result = risk.assess({**self.row, "state": "down"}, self.bands, self.cfg, candle)
        self.assertGreater(result["position_cap"], 0)
        self.assertLessEqual(result["position_cap"], 0.3)          # 试探仓有上限
        self.assertAlmostEqual(result["stop_level"], 95.0 * 0.995, places=3)

    def test_broken_support_is_flat(self):
        result = risk.assess({**self.row, "close": 94.0}, self.bands, self.cfg)
        self.assertEqual(result["position_cap"], 0)
        self.assertIn("形态失效", result["reason"])

    def test_bad_quality_is_flat(self):
        result = risk.assess({**self.row, "quality_flag": "suspect"}, self.bands, self.cfg)
        self.assertEqual(result["position_cap"], 0)
        self.assertIn("数据不可信", result["reason"])

    def test_high_volatility_scales_position_down(self):
        calm = risk.assess(self.row, self.bands, self.cfg)
        wild = risk.assess({**self.row, "atr_pct": 8.0}, self.bands, self.cfg)
        self.assertLess(wild["position_cap"], calm["position_cap"])

    def test_poor_reward_risk_scales_position_down(self):
        good = risk.assess(self.row, self.bands, self.cfg)
        # 阻力贴着头顶，盈亏比很差
        poor_bands = [self.bands[0], {"level_type": "resistance", "price_low": 100.5,
                                     "price_high": 101.0, "weight": 1.0}]
        poor = risk.assess(self.row, poor_bands, self.cfg)
        self.assertLess(poor["position_cap"], good["position_cap"])
        self.assertLess(poor["risk_reward"], good["risk_reward"])

    def test_without_bands_falls_back_to_atr_stop(self):
        result = risk.assess(self.row, [], self.cfg)
        self.assertGreater(result["position_cap"], 0)
        self.assertAlmostEqual(result["stop_level"], 100.0 - 2 * 2.0, places=3)
        self.assertIn("ATR", result["reason"])

    def test_config_can_override_base_cap(self):
        tight = risk.assess(self.row, self.bands, {"risk": {"base_cap": {"up": 0.4}}})
        roomy = risk.assess(self.row, self.bands, {"risk": {"base_cap": {"up": 1.0}}})
        self.assertLess(tight["position_cap"], roomy["position_cap"])

    def test_describe_is_readable(self):
        text = risk.describe(risk.assess(self.row, self.bands, self.cfg))
        self.assertIn("仓位上限", text)
        self.assertIn("止损", text)
        self.assertIn("先不建仓",
                      risk.describe(risk.assess({**self.row, "state": "down"}, self.bands, self.cfg)))


if __name__ == "__main__":
    unittest.main()
