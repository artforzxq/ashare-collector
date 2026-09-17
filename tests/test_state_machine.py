import unittest

from collector.features import classify, _apply_state_machine
from collector.features import compute_feature_series
from collector.registry import FactorRegistry

PARAMS = {
    "enter_up": 70,
    "exit_up": 55,
    "enter_down": 30,
    "exit_down": 45,
    "confirm_days": 2,
    "min_state_days": 3,
}


class ClassifyTests(unittest.TestCase):
    def test_hysteresis_band_keeps_previous_state(self):
        # 分数 60 落在 55–70 的迟滞带内：从震荡看是震荡，从上升看仍是上升
        self.assertEqual(classify(60, "range", PARAMS), "range")
        self.assertEqual(classify(60, "up", PARAMS), "up")
        self.assertEqual(classify(44, "down", PARAMS), "down")   # 低于 45 的退出阈值，维持下跌
        self.assertEqual(classify(45, "down", PARAMS), "range")  # 回到退出阈值即离开下跌
        self.assertEqual(classify(45, "range", PARAMS), "range")

    def test_threshold_crossing(self):
        self.assertEqual(classify(72, "range", PARAMS), "up")
        self.assertEqual(classify(52, "up", PARAMS), "range")
        self.assertEqual(classify(28, "range", PARAMS), "down")
        self.assertEqual(classify(72, "down", PARAMS), "up")


class MachineTests(unittest.TestCase):
    def _rows(self, scores):
        rows = [
            {"trade_date": f"2026-01-{index + 1:02d}", "trend_score": score}
            for index, score in enumerate(scores)
        ]
        _apply_state_machine(rows, PARAMS)
        return rows

    def test_requires_confirmation_and_min_duration(self):
        rows = self._rows([50, 50, 50, 75, 50, 50])
        self.assertEqual(rows[3]["state"], "range")   # 第 1 天只算待确认
        self.assertEqual(rows[3]["pending_days"], 1)
        self.assertEqual(rows[4]["state"], "range")   # 第 2 天回调，确认中断
        self.assertEqual(rows[4]["pending_days"], 0)

    def test_switch_after_two_confirmations(self):
        rows = self._rows([50, 50, 50, 75, 76, 78, 80])
        self.assertEqual(rows[4]["state"], "up")
        self.assertTrue(rows[4]["state_switched"])
        self.assertEqual(rows[6]["state"], "up")

    def test_hysteresis_reduces_whipsaw(self):
        scores = [42, 45, 51, 58, 66, 71, 74, 69, 72, 76, 71, 66, 58, 52, 47, 44, 48, 55, 61, 68, 72, 75, 73, 68, 62, 57, 63, 70, 74, 78]
        naive = ["up" if s >= 70 else ("down" if s <= 30 else "range") for s in scores]
        rows = self._rows(scores)
        machine = [row["state"] for row in rows]

        def flips(sequence):
            return sum(1 for i in range(1, len(sequence)) if sequence[i] != sequence[i - 1])

        self.assertLess(flips(machine), flips(naive))


class FeatureSeriesTests(unittest.TestCase):
    def test_scores_and_states_are_produced(self):
        cfg = {
            "state": PARAMS,
            "factors": [
                {
                    "factor_id": "ma_slope",
                    "layer": "state",
                    "role": "primary",
                    "category": "trend",
                    "weight": 1.0,
                    "status": "active",
                    "source_field": "ma_slope_20",
                    "normalize": {"type": "linear", "x0": -3.0, "x1": 3.0},
                }
            ],
        }
        registry = FactorRegistry(cfg["factors"])
        bars = []
        price = 10.0
        for index in range(160):
            price *= 1.004
            bars.append(
                {
                    "code": "TEST",
                    "trade_date": f"2026-{(index // 28) + 1:02d}-{(index % 28) + 1:02d}",
                    "open": price,
                    "high": price * 1.01,
                    "low": price * 0.99,
                    "close": price,
                    "close_adj": price,
                    "amount": 1e8,
                    "pct_chg": 0.4,
                    "quality_flag": "ok",
                }
            )
        series = compute_feature_series(bars, cfg, registry)

        self.assertEqual(len(series), 160)
        self.assertIsNone(series[10]["trend_score"])          # 历史不足
        self.assertIsNotNone(series[-1]["trend_score"])
        self.assertEqual(series[-1]["state"], "up")
        self.assertGreater(series[-1]["trend_score"], 70)
        self.assertTrue(any(c["factor_id"] == "ma_slope" for c in series[-1]["contributions"]))


if __name__ == "__main__":
    unittest.main()
