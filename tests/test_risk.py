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
        self.assertAlmostEqual(result["stop_level"], 100.0 - 2 * 2.0, places=3)
        self.assertIn("ATR", result["reason"])
        # 没有关键带 → 目标按兜底的 1R 算 → 盈亏比 1.0；
        # 1.0 的盈亏比配 50% 胜率期望正好是 0，所以这里是 0 仓（不是"跳过检查"）。
        self.assertEqual(result["position_cap"], 0.0)
        self.assertIn("期望", result["reason"])
        # 胜率给高一点，同一个标的就能拿到仓位——证明决定权在期望值上
        better = risk.assess(self.row, [], {"risk": {"win_rate": 0.6}})
        self.assertGreater(better["position_cap"], 0)

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

    def test_missing_resistance_band_does_not_skip_the_reward_risk_check(self):
        """上方没有阻力带时，风控不能在"没有数据"的时候放行。

        以前这种票会直接跳过盈亏比这一环——实测观察池里就有两只（有仓位、但头顶没有
        识别出阻力带），等于没经过"上方还有多少空间"的检查就拿到了全额仓位。
        现在按保守倍数估收益空间（config: risk.no_resistance_rr，默认 1R），缩放照常生效。
        """
        bands = [b for b in self.bands if b["level_type"] == "support"]
        result = risk.assess(self.row, bands, self.cfg)
        self.assertIsNotNone(result["risk_reward"])       # 不再是 None
        self.assertAlmostEqual(result["risk_reward"], 1.0, places=2)
        with_resistance = risk.assess(self.row, self.bands, self.cfg)
        self.assertLess(result["position_cap"], with_resistance["position_cap"])
        self.assertTrue(any("无阻力带" in note for note in result["notes"]))

    def test_the_fallback_multiplier_is_configurable(self):
        bands = [b for b in self.bands if b["level_type"] == "support"]
        looser = risk.assess(self.row, bands, {"risk": {"no_resistance_rr": 2.0}})
        self.assertAlmostEqual(looser["risk_reward"], 2.0, places=2)
        self.assertGreater(looser["position_cap"], risk.assess(self.row, bands, self.cfg)["position_cap"])

    def test_near_high_without_overhead_band_is_not_systematically_vetoed(self):
        """实测回归：SZ000333 离 250 日高点只差 5.4%、头顶没有阻力带，旧规则长期 0 仓。

        头顶没有阻力带有两种成因：贴着历史高点（成交密集区全在脚下，上方是真空）和
        分箱没挑出头顶那段（数据缺失）。前者按保守 1R 算是错的——期望正好 0，
        于是"越强的票越做不了"，等于把最强的标的系统性排除在外。
        """
        bands = [b for b in self.bands if b["level_type"] == "support"]
        result = risk.assess({**self.row, "dist_to_high_250": -5.4}, bands, self.cfg)
        self.assertGreater(result["position_cap"], 0)
        self.assertGreater(result["risk_reward"], 1.0)
        self.assertTrue(any("上方真空" in note for note in result["notes"]))

    def test_far_from_high_target_is_capped_by_distance_to_high(self):
        """离 250 日高点还远时，目标不超过"到高点的距离"——头顶一定有筹码，只是没被分箱挑出来。"""
        bands = [b for b in self.bands if b["level_type"] == "support"]
        row = {**self.row, "atr14": 8.0, "atr_pct": 8.0, "dist_to_high_250": -12.0}
        result = risk.assess(row, bands, self.cfg)
        # 到高点 12.0 元 vs 3 倍 ATR 24.0 元 → 取 12.0；下方风险 100 − 95×0.995 = 5.475 元
        self.assertAlmostEqual(result["risk_reward"], 2.19, places=2)
        self.assertTrue(any("较小者" in note for note in result["notes"]))
        # 容差放宽到 15% 后，同一只票算"贴着高点"，改按波动率估目标
        wider = risk.assess(row, bands, {"risk": {"open_space_high_tolerance_pct": 15.0}})
        self.assertGreater(wider["risk_reward"], result["risk_reward"])

    def test_open_space_multiple_is_configurable(self):
        bands = [b for b in self.bands if b["level_type"] == "support"]
        row = {**self.row, "atr14": 8.0, "atr_pct": 8.0, "dist_to_high_250": -40.0}
        default = risk.assess(row, bands, self.cfg)                                    # 3 倍 ATR = 24 元
        roomier = risk.assess(row, bands, {"risk": {"open_space_atr_multiple": 6.0}})  # 6 倍 ATR = 48 元
        self.assertGreater(roomier["risk_reward"], default["risk_reward"])


if __name__ == "__main__":
    unittest.main()
