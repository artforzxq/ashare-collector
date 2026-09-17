import unittest

from collector.alerts import Candidate, apply_budget, apply_cooldown
from collector.arbitrate import DecisionContext, arbitrate


def buy_candidate(level="P1", opportunity=0.75, signal="BREAKOUT_CONFIRMED"):
    return Candidate("SH000300", signal, level, 3900.0, "测试信号", "up", opportunity)


class RuleTests(unittest.TestCase):
    def test_r0_freezes_everything_but_data_anomaly(self):
        ctx = DecisionContext(trade_date="2026-09-16", quality_flag="suspect")
        decision = arbitrate([buy_candidate()], ctx)
        self.assertEqual(decision.accepted, [])
        self.assertEqual(decision.suppressed[0]["rule"], "R0_DATA_HEALTH")

        anomaly = Candidate("SH000300", "DATA_ANOMALY", "P0", 3900.0, "数据异常", "range", 0.5)
        decision = arbitrate([anomaly], ctx)
        self.assertEqual(len(decision.accepted), 1)

    def test_r1_risk_veto_blocks_buy_signals(self):
        ctx = DecisionContext(trade_date="2026-09-16", risk_veto=True)
        decision = arbitrate([buy_candidate(), Candidate("SH000300", "STOP_BREACH", "P0", 3800.0, "跌破", "up", 0.6)], ctx)
        self.assertEqual([c.signal_type for c in decision.accepted], ["STOP_BREACH"])
        self.assertEqual(decision.suppressed[0]["rule"], "R1_RISK_VETO")

    def test_r2_downgrades_buy_signals_in_downtrend(self):
        ctx = DecisionContext(trade_date="2026-09-16", state="down")
        decision = arbitrate([buy_candidate(level="P1", opportunity=0.9)], ctx)
        self.assertEqual(len(decision.accepted), 1)
        self.assertEqual(decision.accepted[0].level, "P2")
        self.assertEqual(decision.logs[0]["rule"], "R2_STATE_PRIORITY")

    def test_r3_default_silence_in_neutral_band(self):
        ctx = DecisionContext(trade_date="2026-09-16", neutral_band=0.10)
        decision = arbitrate([buy_candidate(opportunity=0.53)], ctx)
        self.assertEqual(decision.accepted, [])
        self.assertEqual(decision.suppressed[0]["rule"], "R3_NEUTRAL_SILENCE")

    def test_p0_passes_when_opportunity_is_neutral(self):
        ctx = DecisionContext(trade_date="2026-09-16")
        candidate = Candidate("SH000300", "STOP_BREACH", "P0", 3800.0, "跌破支撑", "up", 0.5)
        decision = arbitrate([candidate], ctx)
        self.assertEqual(len(decision.accepted), 1)


class BudgetTests(unittest.TestCase):
    def test_p1_budget_keeps_highest_opportunity(self):
        cfg = {"alerts": {"budget_p1": 2, "cooldown_days_p1": 1, "cooldown_days_p2": 5, "cooldown_minutes_p0": 15}}
        candidates = [
            buy_candidate(opportunity=0.6, signal="SHELF_DETECTED"),
            buy_candidate(opportunity=0.9, signal="PULLBACK_TO_SUPPORT"),
            buy_candidate(opportunity=0.8, signal="BREAKOUT_CONFIRMED"),
        ]
        kept, dropped = apply_budget(candidates, cfg)
        self.assertEqual(len(kept), 2)
        self.assertEqual({c.signal_type for c in kept}, {"PULLBACK_TO_SUPPORT", "BREAKOUT_CONFIRMED"})
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0]["rule"], "R6_BUDGET")

    def test_p0_never_dropped(self):
        cfg = {"alerts": {"budget_p1": 1, "cooldown_days_p1": 1, "cooldown_days_p2": 5, "cooldown_minutes_p0": 15}}
        candidates = [Candidate("SH000300", "STOP_BREACH", "P0", 3800.0, "跌破", "up", 0.5)] + [
            buy_candidate(opportunity=0.9, signal=f"S{i}") for i in range(5)
        ]
        kept, _ = apply_budget(candidates, cfg)
        self.assertEqual(len([c for c in kept if c.level == "P0"]), 1)


if __name__ == "__main__":
    unittest.main()
