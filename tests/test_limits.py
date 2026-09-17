"""涨跌停判定与可成交性（纯计算，不需要数据库）。"""

import unittest

from collector import limits


class LimitPctTests(unittest.TestCase):
    def test_board_comes_from_code_prefix(self):
        self.assertEqual(limits.limit_pct("SH600000"), 10.0)     # 沪市主板
        self.assertEqual(limits.limit_pct("SZ000001"), 10.0)     # 深市主板
        self.assertEqual(limits.limit_pct("SH688981"), 20.0)     # 科创板
        self.assertEqual(limits.limit_pct("SZ300750"), 20.0)     # 创业板
        self.assertEqual(limits.limit_pct("BJ430047"), 30.0)     # 北交所

    def test_st_only_narrows_the_main_board(self):
        self.assertEqual(limits.limit_pct("SH600000", "ST 中安"), 5.0)
        self.assertEqual(limits.limit_pct("SZ000001", "*ST 某某"), 5.0)
        # 创业板 / 科创板的 ST 仍然是 20%
        self.assertEqual(limits.limit_pct("SZ300001", "ST 创业"), 20.0)
        self.assertEqual(limits.limit_pct("SH688981", "ST 科创"), 20.0)


class LimitPriceTests(unittest.TestCase):
    def test_rounds_half_up_to_cent(self):
        # 2.435 × 1.1 = 2.6785 → 2.68。内置 round() 是银行家舍入，交易所口径是四舍五入，
        # 所以这里必须显式指定，不能直接用 round()。
        self.assertAlmostEqual(limits.limit_price(2.435, 10.0), 2.68, places=2)
        self.assertAlmostEqual(limits.limit_price(10.00, 10.0), 11.00, places=2)
        self.assertAlmostEqual(limits.limit_price(10.00, 10.0, up=False), 9.00, places=2)
        self.assertAlmostEqual(limits.limit_price(3.33, 20.0), 4.00, places=2)


class FillabilityTests(unittest.TestCase):
    @staticmethod
    def _bar(close, pre_close, pct_chg=None):
        return {"close": close, "pre_close": pre_close, "pct_chg": pct_chg}

    def test_limit_up_needs_the_exact_rounded_price(self):
        self.assertTrue(limits.at_limit_up(self._bar(11.00, 10.00), "SH600000"))
        self.assertFalse(limits.at_limit_up(self._bar(10.98, 10.00), "SH600000"))

    def test_growth_board_has_a_wider_band(self):
        # 同样的 +10% 在创业板不是涨停，+20% 才是
        self.assertFalse(limits.at_limit_up(self._bar(11.00, 10.00), "SZ300001"))
        self.assertTrue(limits.at_limit_up(self._bar(12.00, 10.00), "SZ300001"))

    def test_limit_down_mirrors_limit_up(self):
        self.assertTrue(limits.at_limit_down(self._bar(9.00, 10.00), "SH600000"))
        self.assertFalse(limits.at_limit_down(self._bar(9.02, 10.00), "SH600000"))

    def test_falls_back_to_pct_chg_when_pre_close_missing(self):
        self.assertTrue(limits.at_limit_up({"close": 11.0, "pre_close": None, "pct_chg": 10.0}, "SH600000"))
        self.assertFalse(limits.at_limit_up({"close": 11.0, "pre_close": None, "pct_chg": 3.0}, "SH600000"))
        self.assertTrue(limits.at_limit_down({"close": 9.0, "pre_close": None, "pct_chg": -10.0}, "SH600000"))

    def test_missing_data_is_treated_as_tradeable(self):
        # 判不出来就不挡路：默认态度是"能成交"，宁可乐观也不要凭空吞掉信号
        self.assertFalse(limits.at_limit_up({"close": None}, "SH600000"))
        self.assertFalse(limits.at_limit_up({"close": 10.0}, "SH600000"))


if __name__ == "__main__":
    unittest.main()
