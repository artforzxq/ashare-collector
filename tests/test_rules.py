"""规则引擎：筛选条件写成数据之后，求值必须稳、可预测。"""

import unittest

from collector import rules

ROW = {
    "code": "SH600000", "close": 10.0, "ma20": 9.0, "ma60": 12.0,
    "state": "range", "trend_score": 55.0, "vol_shrink_ratio": 0.5,
    "consolidation_days": 30, "breakout_confirmed": 0, "empty_field": None,
    "raw_values": {"dist_to_level": -1.2},
    "candle": {"reversal_up": True, "pattern": "放量长阳"},
}


class OperatorTests(unittest.TestCase):
    def test_comparison_operators(self):
        self.assertTrue(rules.check(ROW, {"field": "close", "op": "==", "value": 10.0}))
        self.assertTrue(rules.check(ROW, {"field": "state", "op": "!=", "value": "up"}))
        self.assertTrue(rules.check(ROW, {"field": "trend_score", "op": ">=", "value": 50}))
        self.assertTrue(rules.check(ROW, {"field": "consolidation_days", "op": ">", "value": 20}))
        self.assertTrue(rules.check(ROW, {"field": "vol_shrink_ratio", "op": "<=", "value": 0.6}))
        self.assertFalse(rules.check(ROW, {"field": "state", "op": "==", "value": "up"}))

    def test_compare_two_fields_with_factor(self):
        # close > ma60 × 0.5 → 10 > 6 成立；close > ma60 × 1.02 → 10 > 12.24 不成立
        self.assertTrue(rules.check(ROW, {"field": "close", "op": ">", "compare": "ma60", "factor": 0.5}))
        self.assertFalse(rules.check(ROW, {"field": "close", "op": ">", "compare": "ma60", "factor": 1.02}))

    def test_between_and_membership(self):
        self.assertTrue(rules.check(ROW, {"field": "trend_score", "op": "between", "value": [50, 60]}))
        self.assertFalse(rules.check(ROW, {"field": "trend_score", "op": "between", "value": [60, 70]}))
        self.assertTrue(rules.check(ROW, {"field": "state", "op": "in", "value": ["range", "up"]}))
        self.assertTrue(rules.check(ROW, {"field": "state", "op": "not_in", "value": ["down"]}))

    def test_text_operators(self):
        self.assertTrue(rules.check(ROW, {"field": "code", "op": "startswith", "value": "SH"}))
        self.assertTrue(rules.check(ROW, {"field": "code", "op": "contains", "value": "600"}))

    def test_dotted_path_and_exists(self):
        self.assertTrue(rules.check(ROW, {"field": "candle.reversal_up", "op": "==", "value": True}))
        self.assertTrue(rules.check(ROW, {"field": "raw_values.dist_to_level", "op": "between",
                                          "value": [-1.5, 1.5]}))
        self.assertTrue(rules.check(ROW, {"field": "candle.reversal_up", "op": "exists", "value": True}))
        self.assertTrue(rules.check(ROW, {"field": "empty_field", "op": "exists", "value": False}))
        self.assertIsNone(rules.get_value(ROW, "nope.nope"))

    def test_missing_values_never_pass(self):
        """取不到值的字段一律判否——宁可漏筛，不要把空值当通过。"""
        for op, value in (("==", 1), (">", 0), ("between", [0, 9]), ("in", [1, 2])):
            self.assertFalse(rules.check(ROW, {"field": "empty_field", "op": op, "value": value}))
        self.assertFalse(rules.check(ROW, {"field": "missing_field", "op": "!=", "value": 1}))

    def test_unknown_operator_raises(self):
        with self.assertRaises(ValueError):
            rules.check(ROW, {"field": "close", "op": "~=", "value": 1})


class MatchTests(unittest.TestCase):
    def test_all_conditions_must_hold(self):
        conditions = [
            {"field": "state", "op": "!=", "value": "up"},
            {"field": "consolidation_days", "op": ">=", "value": 20},
            {"field": "vol_shrink_ratio", "op": "<=", "value": 0.6},
        ]
        self.assertTrue(rules.matches(ROW, conditions))
        self.assertFalse(rules.matches(ROW, conditions + [{"field": "close", "op": ">", "value": 99}]))

    def test_empty_conditions_do_not_match(self):
        """空条件绝不能命中——否则配置写错会筛出全市场。"""
        self.assertFalse(rules.matches(ROW, []))
        self.assertFalse(rules.matches(ROW, None))

    def test_describe_reads_like_chinese(self):
        text = rules.describe_all([
            {"field": "state", "op": "!=", "value": "up"},
            {"field": "close", "op": "<=", "compare": "ma20", "factor": 1.12},
        ])
        self.assertIn("state ≠ up", text)
        self.assertIn("ma20×1.12", text)


if __name__ == "__main__":
    unittest.main()
