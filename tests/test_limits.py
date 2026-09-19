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

    # ---- 上市阶段：新股头几天规则不一样，别拿老口径硬套 ----

    def test_main_board_first_day_uses_the_ipo_day_limit(self):
        """主板首日是按发行价 ±44%/36%，不是 10%。"""
        self.assertEqual(limits.limit_pct("SH601091", "沈鼓集团", 1, up=True), 44.0)
        self.assertEqual(limits.limit_pct("SH601091", "沈鼓集团", 1, up=False), 36.0)
        self.assertEqual(limits.limit_pct("SH601091", "沈鼓集团", 2), 10.0)      # 次日起回到 10%

    def test_star_and_gem_have_no_limit_for_the_first_five_days(self):
        for code in ("SH688111", "SZ300750"):
            self.assertIsNone(limits.limit_pct(code, None, 1))
            self.assertIsNone(limits.limit_pct(code, None, 5))
            self.assertEqual(limits.limit_pct(code, None, 6), 20.0)
        # 不设限的那几天谈不上"封板"，哪怕涨了 50%
        self.assertFalse(limits.at_limit_up({"close": 15.0, "pre_close": 10.0}, "SZ300750", trading_days=1))
        self.assertTrue(limits.at_limit_up({"close": 12.0, "pre_close": 10.0}, "SZ300750", trading_days=6))

    def test_bj_first_day_has_no_limit_then_thirty_percent(self):
        self.assertIsNone(limits.limit_pct("BJ920298", None, 1))
        self.assertEqual(limits.limit_pct("BJ920298", None, 2), 30.0)
        self.assertTrue(limits.at_limit_up({"close": 13.0, "pre_close": 10.0}, "BJ920298", trading_days=2))

    def test_unknown_listing_age_falls_back_to_board_rules(self):
        self.assertEqual(limits.limit_pct("SH600000"), 10.0)
        self.assertEqual(limits.limit_pct("SZ300001"), 20.0)
        self.assertEqual(limits.limit_pct("BJ430001"), 30.0)

    def test_max_move_uses_the_legal_limit_plus_tolerance(self):
        cfg = {"validation": {"jump_tol_pct": 1.0, "jump_pct_limit_unlimited": 300.0}}
        self.assertEqual(limits.max_move_pct("SH600000", None, None, cfg), 11.0)
        self.assertEqual(limits.max_move_pct("SZ300001", None, None, cfg), 21.0)
        self.assertEqual(limits.max_move_pct("BJ430001", None, None, cfg), 31.0)
        # 新股不设限的那几天没有"上限"，但仍要拦住离谱数据
        self.assertEqual(limits.max_move_pct("SZ300750", None, 1, cfg), 300.0)
        # ST 主板是 5%
        self.assertEqual(limits.max_move_pct("SH600000", "ST某某", None, cfg), 6.0)

    def test_missing_data_is_treated_as_tradeable(self):
        # 判不出来就不挡路：默认态度是"能成交"，宁可乐观也不要凭空吞掉信号
        self.assertFalse(limits.at_limit_up({"close": None}, "SH600000"))
        self.assertFalse(limits.at_limit_up({"close": 10.0}, "SH600000"))


if __name__ == "__main__":
    unittest.main()
