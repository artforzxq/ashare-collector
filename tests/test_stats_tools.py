"""统计工具：按交易日聚合的 t/置信区间、多重检验惩罚、残差 IC。

这三样是这轮加进来的"防自欺"工具，共同点是**都不改变数据，只改变你敢下多大的结论**：
  · 按交易日聚合：同一天几千只票高度相关，按样本算误差棒会窄得离谱；
  · 多重检验惩罚：扫了 N 组取最好的，本身就在数据里挑噪声；
  · 残差 IC：相关系数 0.65 就该怀疑是同一个因子换了名字。
"""

import unittest

from collector import backtest, promotion


class DailyStatsTests(unittest.TestCase):
    def test_mean_and_t_use_days_not_samples(self):
        """一天几千个样本和一天一个样本，在"日"这个维度上权重相同。"""
        by_day = {"2026-09-01": [1.0] * 100, "2026-09-02": [3.0] * 2}
        stats = backtest.daily_stats(by_day)
        self.assertEqual(stats["days"], 2)
        self.assertAlmostEqual(stats["mean"], 2.0)          # (1 + 3) / 2，不是按样本加权
        self.assertIsNotNone(stats["t"])

    def test_wide_confidence_interval_when_days_are_few(self):
        few = backtest.daily_stats({"2026-09-01": [1.0], "2026-09-02": [-1.0]})
        many = backtest.daily_stats({f"2026-09-{i + 1:02d}": [1.0 if i % 2 else -1.0] for i in range(20)})
        self.assertGreater(few["ci95"], many["ci95"])       # 样本少 → 误差棒宽

    def test_single_day_gives_no_t_value(self):
        stats = backtest.daily_stats({"2026-09-01": [1.0, 2.0]})
        self.assertEqual(stats["days"], 1)
        self.assertIsNone(stats["t"])

    def test_p_value_and_critical_t_are_consistent(self):
        self.assertAlmostEqual(backtest.normal_two_sided_p(0.0), 1.0, places=4)
        self.assertLess(backtest.normal_two_sided_p(3.0), 0.01)
        self.assertAlmostEqual(backtest._z_for_two_sided(0.05), 1.96, places=1)


class SignificanceTests(unittest.TestCase):
    """扫了 N 组之后，最好那组还站得住吗。"""

    def _results(self, t_value):
        return [{"label": "best", "excess20": 1.0, "t20": t_value, "days20": 400}]

    def test_ordinary_t_is_no_longer_significant_after_correction(self):
        """t=2.3 单独看是显著的，但扫了 36 组之后就不够了——这正是数据窥探。"""
        single = backtest.significance(self._results(2.3), searched=1)
        many = backtest.significance(self._results(2.3), searched=36)
        self.assertTrue(single["significant"])
        self.assertFalse(many["significant"])
        self.assertGreater(many["p_adj"], single["p_adj"])
        self.assertGreater(many["crit_t"], single["crit_t"])

    def test_strong_t_survives_the_correction(self):
        result = backtest.significance(self._results(4.5), searched=36)
        self.assertTrue(result["significant"])

    def test_no_t_value_is_not_a_crash(self):
        result = backtest.significance([{"label": "x", "t20": None, "days20": 0}], searched=10)
        self.assertFalse(result["ok"])
        self.assertIsNone(result["p_adj"])


class PartialCorrTests(unittest.TestCase):
    def test_removes_the_part_explained_by_the_partner(self):
        """因子 = 伙伴 + 一点噪声，收益也只跟伙伴有关 → 剔掉伙伴后应该几乎没关系。"""
        triples = []
        for i in range(60):
            partner = float(i)
            noise = 0.3 if i % 2 else -0.3
            forward = partner * 0.5 + (0.2 if i % 3 else -0.2)   # 收益自己也要有残差，否则相关系数没定义
            triples.append((partner + noise, forward, partner))
        partial = promotion._partial_corr(triples)
        self.assertIsNotNone(partial)
        self.assertLess(abs(partial), 0.5)

    def test_keeps_the_part_that_is_not_explained(self):
        """因子 = 伙伴 + 独立信号：剔掉伙伴之后仍与收益相关。"""
        triples = [(float(i) + (10.0 if i % 2 else 0.0), float(i % 2) * 5.0, float(i)) for i in range(60)]
        partial = promotion._partial_corr(triples)
        self.assertGreater(abs(partial or 0), 0.5)

    def test_too_few_points_gives_no_conclusion(self):
        self.assertIsNone(promotion._partial_corr([(1.0, 2.0, 3.0)] * 5))


if __name__ == "__main__":
    unittest.main()
