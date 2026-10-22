"""成本口径：全项目只算一次，且小额单笔要看见佣金下限的惩罚。"""

import unittest

from collector import costs


class CostTests(unittest.TestCase):
    def test_default_round_trip_is_0302_percent(self):
        # 佣金 2.5bp×2 + 印花 5bp + 过户 0.1bp×2 + 滑点 20bp = 30.2bp
        self.assertAlmostEqual(costs.round_trip_pct({}), 0.302, places=6)
        self.assertIn("滑点", costs.describe({}))

    def test_config_overrides_only_given_keys(self):
        cfg = {"backtest": {"cost": {"slippage_bps": 0}}}
        self.assertAlmostEqual(costs.round_trip_pct(cfg), 0.102, places=6)

    def test_min_commission_punishes_small_notional(self):
        # 8000 元单笔：5 元佣金 = 单边 0.0625%，往返 0.125%
        # 合计 = 0.125 + 0.05 + 0.002 + 0.2 = 0.377%
        self.assertAlmostEqual(costs.round_trip_pct({}, notional=8000), 0.377, places=4)
        # 金额够大时，最低佣金不再起作用，回到 0.302%
        self.assertAlmostEqual(costs.round_trip_pct({}, notional=1_000_000), 0.302, places=4)

    def test_cost_in_r_uses_stop_width(self):
        # 0.302% ÷ 8% = 0.03775R
        self.assertAlmostEqual(costs.cost_in_r({}, stop_pct=8.0), 0.03775, places=5)
        # 止损越窄，成本占 R 的比例越高——窄止损高频结构上最差
        self.assertGreater(costs.cost_in_r({}, stop_pct=3.0),
                           costs.cost_in_r({}, stop_pct=10.0))
        self.assertIsNone(costs.cost_in_r({}, stop_pct=0))


if __name__ == "__main__":
    unittest.main()
