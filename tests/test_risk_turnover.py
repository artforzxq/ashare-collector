"""换手放量折价：负向信号走风险层，只缩仓位、不否决。

依据是本地回测实测（那把尺子本身由 tests/test_turnover_study.py 测）：
换手放大到 2 倍以上之后 5 日平均跑输同批标的 0.68pp，3 倍以上跑输 1.73pp。
所以这里测的是"它有没有老实按配置打折、理由有没有写清楚"。
"""

import unittest

from collector import risk

CFG = {
    "risk": {
        "base_cap": {"up": 0.8, "range": 0.4, "down": 0.0},
        "target_atr_pct": 2.0,
        "win_rate": 0.5,
        "target_expectancy": 0.5,
        "turnover_discount": {"enabled": True, "ratio": 2.0, "factor": 0.6, "high_position_pct": None},
    }
}
BANDS = [{"level_type": "support", "price_low": 66.0, "price_high": 69.0, "weight": 1.0}]


def _row(**over):
    row = {
        "code": "SH600487", "close": 69.49, "state": "range", "atr14": 2.0, "atr_pct": 2.9,
        "dist_to_high_250": -8.0, "quality_flag": "ok", "turnover_ratio": None,
    }
    row.update(over)
    return row


class TurnoverDiscountTests(unittest.TestCase):
    def test_below_threshold_is_untouched(self):
        calm = risk.assess(_row(turnover_ratio=1.2), BANDS, CFG)
        none = risk.assess(_row(turnover_ratio=None), BANDS, CFG)
        self.assertEqual(calm["position_cap"], none["position_cap"])
        self.assertNotIn("换手放量", calm["reason"])

    def test_spike_cuts_the_cap_by_the_configured_factor(self):
        calm = risk.assess(_row(turnover_ratio=1.0), BANDS, CFG)
        spiked = risk.assess(_row(turnover_ratio=2.5), BANDS, CFG)
        self.assertAlmostEqual(spiked["position_cap"], round(calm["position_cap"] * 0.6, 3), places=3)
        self.assertIn("换手放量 2.5 倍", spiked["reason"])
        self.assertTrue(any("换手放量" in note for note in spiked["notes"]))

    def test_it_scales_but_never_vetoes(self):
        """负向信号只回答"做多大"：即使放量 10 倍，也不该直接判 0 仓。"""
        spiked = risk.assess(_row(turnover_ratio=10.0), BANDS, CFG)
        self.assertGreater(spiked["position_cap"], 0)

    def test_can_be_switched_off(self):
        cfg = {"risk": {**CFG["risk"], "turnover_discount": {"enabled": False}}}
        off = risk.assess(_row(turnover_ratio=5.0), BANDS, cfg)
        on = risk.assess(_row(turnover_ratio=5.0), BANDS, CFG)
        self.assertGreater(off["position_cap"], on["position_cap"])

    def test_optional_high_position_gate(self):
        """high_position_pct 是可选的收窄条件（默认关着，因为没测过）。"""
        cfg = {"risk": {**CFG["risk"],
                        "turnover_discount": {"enabled": True, "ratio": 2.0, "factor": 0.6,
                                              "high_position_pct": 5}}}
        low = risk.assess(_row(turnover_ratio=3.0, dist_to_high_250=-12.0), BANDS, cfg)
        near = risk.assess(_row(turnover_ratio=3.0, dist_to_high_250=-2.0), BANDS, cfg)
        base = risk.assess(_row(turnover_ratio=1.0, dist_to_high_250=-2.0), BANDS, cfg)
        self.assertEqual(low["position_cap"], base["position_cap"])       # 离高点还远 → 不打折
        self.assertLess(near["position_cap"], base["position_cap"])       # 贴着高点 → 打折

    def test_daily_report_surfaces_the_reason(self):
        """打折的理由要能出现在简报里——不然仓位变小了没人知道为什么。"""
        spiked = risk.assess(_row(turnover_ratio=4.0), BANDS, CFG)
        self.assertIn("换手放量 4.0 倍", risk.describe(spiked))


if __name__ == "__main__":
    unittest.main()
