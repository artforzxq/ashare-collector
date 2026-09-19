"""结构低点参与止损：只收紧、不放宽。

支撑带是成交量密集区聚类出来的，天生滞后——价格从底部抬起 20% 之后，
脚下那条带还在很远的地方，止损一放就是 8% 的风险预算。
摆动低点是这一波自己走出来的位置，用它当止损更近、也更贴近"这次上来的理由"。

所以规则只有一条：**候选止损取更近的那个**。结构低点在支撑带下方时，
原止损不动（不能因为多了个参考位就把风险放大）。
"""

import unittest

from collector import risk

BANDS = [
    {"level_type": "support", "price_low": 95.0, "price_high": 97.0, "weight": 1.0},
    {"level_type": "resistance", "price_low": 120.0, "price_high": 122.0, "weight": 1.0},
]
CFG = {"risk": {}}


def _row(**overrides) -> dict:
    row = {"close": 100.0, "state": "up", "atr_pct": 2.0, "atr14": 2.0,
           "quality_flag": "ok", "swing_low_1": None, "bars_since_swing_low": None}
    row.update(overrides)
    return row


class StructureStopTests(unittest.TestCase):
    def test_nearer_structure_low_tightens_the_stop(self):
        result = risk.assess(_row(swing_low_1=98.0, bars_since_swing_low=12), BANDS, CFG)
        self.assertAlmostEqual(result["stop_level"], 98.0 * 0.995, places=3)
        self.assertIn("结构低点", result["reason"])
        self.assertIn("更近", "；".join(result["notes"]))

    def test_farther_structure_low_does_not_widen_the_stop(self):
        """结构低点在支撑带下方 → 保持原止损。多一个参考位不等于多一份风险。"""
        result = risk.assess(_row(swing_low_1=90.0, bars_since_swing_low=8), BANDS, CFG)
        self.assertAlmostEqual(result["stop_level"], 95.0 * 0.995, places=3)
        self.assertNotIn("结构低点", result["reason"])

    def test_tighter_stop_improves_expectancy(self):
        wide = risk.assess(_row(), BANDS, CFG)
        tight = risk.assess(_row(swing_low_1=98.0, bars_since_swing_low=12), BANDS, CFG)
        self.assertGreater(tight["risk_reward"], wide["risk_reward"])
        self.assertGreaterEqual(tight["position_cap"], wide["position_cap"])

    def test_stale_structure_low_is_ignored(self):
        result = risk.assess(_row(swing_low_1=98.0, bars_since_swing_low=200), BANDS, CFG)
        self.assertAlmostEqual(result["stop_level"], 95.0 * 0.995, places=3)

    def test_too_close_structure_low_is_ignored(self):
        """贴着现价的止损挡不住任何东西，只会被噪声扫掉。"""
        result = risk.assess(_row(swing_low_1=99.9, bars_since_swing_low=3), BANDS, CFG)
        self.assertAlmostEqual(result["stop_level"], 95.0 * 0.995, places=3)

    def test_broken_structure_low_is_ignored(self):
        result = risk.assess(_row(swing_low_1=101.0, bars_since_swing_low=3), BANDS, CFG)
        self.assertAlmostEqual(result["stop_level"], 95.0 * 0.995, places=3)

    def test_missing_swing_columns_do_not_break_risk(self):
        row = _row()
        row.pop("swing_low_1")
        row.pop("bars_since_swing_low")
        result = risk.assess(row, BANDS, CFG)
        self.assertGreater(result["position_cap"], 0)

    def test_switch_can_turn_it_off(self):
        cfg = {"risk": {"structure_stop": {"enabled": False}}}
        result = risk.assess(_row(swing_low_1=98.0, bars_since_swing_low=12), BANDS, cfg)
        self.assertAlmostEqual(result["stop_level"], 95.0 * 0.995, places=3)


if __name__ == "__main__":
    unittest.main()
