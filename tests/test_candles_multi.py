"""三根以上的形态：三法（延续）+ 三重顶底 / 头肩 / 圆弧 / 岛形（反转）。

两类要点：
  1. **图形形态靠摆动点**，摆动点天生滞后（右边要等几根确认），所以它们认出来的
     时候行情已经走了一截——这是代价，不是 bug；
  2. 确认必须是**刚发生的那一次突破/跌破**，不能是"现在处在颈线下方"。
     后者意味着跌下去之后的每一天都算命中——实测单这一条就让三重顶多出十几倍。
"""

import unittest

from collector import candles


def _bar(day: str, open_, high, low, close, volume: float = 1_000_000.0) -> dict:
    return {"code": "SH600000", "trade_date": day, "open": open_, "high": high, "low": low,
            "close": close, "volume": volume, "amount": close * volume}


def _series(rows: list[tuple]) -> list[dict]:
    return [_bar(f"2026-{(index // 28) + 1:02d}-{(index % 28) + 1:02d}", *row)
            for index, row in enumerate(rows)]


def _leg(closes: list[float]) -> list[tuple]:
    rows, previous = [], None
    for close in closes:
        open_ = previous if previous is not None else close
        rows.append((open_, max(open_, close) * 1.002, min(open_, close) * 0.998, close))
        previous = close
    return rows


def _zigzag(pivots: list[float], leg: int = 5) -> list[tuple]:
    """把拐点连成锯齿：每段 leg 根，价格线性走到下一个拐点。"""
    closes: list[float] = []
    for start, end in zip(pivots, pivots[1:]):
        closes += [start + (end - start) * step / leg for step in range(leg)]
    closes.append(pivots[-1])
    return _leg(closes)


def _analyze(rows: list[tuple]) -> dict:
    bars = _series(rows)
    return candles.analyze(bars, len(bars) - 1)


class ThreeMethodsTests(unittest.TestCase):
    """三法：长实体 → 几根小实体整理（不破母线）→ 同向长实体确认。"""

    def test_rising_three_methods(self):
        rows = _leg([10.0] * 3) + [
            (10.0, 11.35, 9.95, 11.3),      # 母线：长阳
            (11.2, 11.3, 11.05, 11.1),      # 整理：小实体，都在母线实体里
            (11.1, 11.2, 10.95, 11.05),
            (11.05, 11.15, 10.9, 11.0),
            (10.9, 11.75, 10.85, 11.7),     # 确认：长阳，收得比母线高
        ]
        result = _analyze(rows)
        self.assertTrue(result["rising_three_methods"])
        self.assertIn("上升三法", result["pattern"])
        # 延续形态，方向语义和反转不同：不算"反转确认"（combo_up 只留给反转类图形）
        self.assertFalse(result["combo_up"])
        self.assertTrue(candles.obvious(result))

    def test_falling_three_methods(self):
        rows = _leg([10.0] * 3) + [
            (11.3, 11.35, 9.95, 10.0),
            (10.15, 10.4, 10.05, 10.1),
            (10.1, 10.3, 9.95, 10.05),
            (10.05, 10.25, 9.95, 10.0),
            (10.5, 10.55, 9.2, 9.25),
        ]
        result = _analyze(rows)
        self.assertTrue(result["falling_three_methods"])
        self.assertIn("下降三法", result["pattern"])
        self.assertFalse(result["combo_down"])

    def test_a_middle_bar_breaking_out_of_the_parent_is_rejected(self):
        """整理段要是跑出了母线，那就不是"歇一口气"，是另起一段行情。"""
        rows = _leg([10.0] * 3) + [
            (10.0, 11.35, 9.95, 11.3),
            (11.25, 12.4, 11.0, 12.3),      # 冲出去了
            (12.3, 12.4, 11.9, 12.0),
            (12.0, 12.1, 11.5, 11.6),
            (11.6, 11.9, 11.4, 11.85),
        ]
        self.assertFalse(_analyze(rows)["rising_three_methods"])


class TripleTopBottomTests(unittest.TestCase):
    """三重顶/底：三个峰（谷）大致同高，之间有明显回撤，**刚跌破（升破）颈线**才算。"""

    TOP = [9.5, 11.0, 10.3, 11.0, 10.35, 11.0, 10.6]

    def _top_bars(self, break_close: float, before_close: float = 10.35) -> list[tuple]:
        rows = _zigzag(self.TOP)
        rows.append(_leg([before_close])[0])
        rows.append(_leg([break_close])[0])
        return rows

    def test_triple_top_needs_a_fresh_break_of_the_neckline(self):
        result = _analyze(self._top_bars(9.9))
        self.assertTrue(result["triple_top"])
        self.assertIn("三重顶", result["pattern"])
        self.assertTrue(result["combo_down"])

    def test_a_second_day_below_the_neckline_does_not_fire_again(self):
        """确认是"刚跌下去"那一次，不是"现在在颈线下方"——否则每天都会重报一次。"""
        rows = self._top_bars(9.9)
        rows.append(_leg([9.8])[0])
        result = _analyze(rows)
        self.assertFalse(result["triple_top"])

    def test_no_break_no_pattern(self):
        rows = self._top_bars(10.5, before_close=10.6)
        self.assertFalse(_analyze(rows)["triple_top"])

    def test_triple_bottom(self):
        pivots = [12.0, 10.0, 10.7, 10.0, 10.65, 10.0, 10.4]
        rows = _zigzag(pivots)
        rows.append(_leg([10.65])[0])
        rows.append(_leg([11.2])[0])
        result = _analyze(rows)
        self.assertTrue(result["triple_bottom"])
        self.assertIn("三重底", result["pattern"])
        self.assertTrue(result["combo_up"])


class HeadShouldersTests(unittest.TestCase):
    """头肩形：中间那个峰（谷）明显更极端，两肩大致同高。"""

    def test_head_shoulders_top(self):
        pivots = [9.5, 10.8, 10.3, 11.6, 10.35, 10.8, 10.6]
        rows = _zigzag(pivots)
        rows.append(_leg([10.35])[0])
        rows.append(_leg([9.9])[0])
        result = _analyze(rows)
        self.assertTrue(result["head_shoulders_top"])
        self.assertIn("头肩顶", result["pattern"])
        self.assertFalse(result["triple_top"])          # 两回事，别互相冒充

    def test_head_shoulders_bottom(self):
        pivots = [12.0, 10.6, 11.2, 9.9, 11.15, 10.6, 10.9]
        rows = _zigzag(pivots)
        rows.append(_leg([11.15])[0])
        rows.append(_leg([11.6])[0])
        result = _analyze(rows)
        self.assertTrue(result["head_shoulders_bottom"])
        self.assertIn("头肩底", result["pattern"])


class RoundingTests(unittest.TestCase):
    """圆弧：把窗口分三段，中段明显凹（凸）下去，而且全程以小实体为主。"""

    def _bowl(self) -> list[tuple]:
        closes = [11.0 - index * 0.06 for index in range(24)]      # 下来
        closes += [9.5 + (0.02 if index % 2 else -0.02) for index in range(18)]   # 碗底（走宽）
        closes += [9.5 + index * 0.03 for index in range(8)]       # 上来一点
        return _leg(closes)

    def test_rounding_bottom_needs_a_fresh_break_above_the_rim(self):
        rows = self._bowl()
        last = rows[-1][3]
        rows.append(_leg([last * 1.06])[0])            # 刚抬离碗底 6%（> arc 3%）
        result = _analyze(rows)
        self.assertTrue(result["rounding_bottom"])
        self.assertIn("圆弧底", result["pattern"])

    def test_a_second_day_above_the_rim_does_not_fire_again(self):
        rows = self._bowl()
        last = rows[-1][3]
        rows.append(_leg([last * 1.06])[0])
        rows.append(_leg([last * 1.10])[0])
        self.assertFalse(_analyze(rows)["rounding_bottom"])

    def test_rounding_top(self):
        closes = [9.0 + index * 0.06 for index in range(24)]
        closes += [10.5 + (0.02 if index % 2 else -0.02) for index in range(18)]
        closes += [10.5 - index * 0.03 for index in range(8)]
        rows = _leg(closes)
        last = rows[-1][3]
        rows.append(_leg([last * 0.94])[0])
        result = _analyze(rows)
        self.assertTrue(result["rounding_top"])
        self.assertIn("圆弧顶", result["pattern"])


class IslandTests(unittest.TestCase):
    """岛形反转：先向下跳空、隔几根再向上跳空，两个缺口价位相近，中间那段成了孤岛。"""

    def test_island_bottom(self):
        rows = _leg([12.0 - index * 0.1 for index in range(12)])   # 下跌
        rows.append((10.55, 10.6, 10.3, 10.4))                     # 向下跳空
        rows.append((10.4, 10.5, 10.2, 10.3))                      # 孤岛（整段留在跳空下方）
        rows.append((10.3, 10.45, 10.15, 10.35))
        rows.append((10.8, 11.2, 10.75, 11.1))                     # 向上跳空，回到跳空前的位置之上
        result = _analyze(rows)
        self.assertTrue(result["island_bottom"])
        self.assertIn("岛形反转（底）", result["pattern"])

    def test_island_top(self):
        rows = _leg([9.0 + index * 0.1 for index in range(12)])
        rows.append((11.2, 11.5, 11.15, 11.4))
        rows.append((11.4, 11.5, 11.25, 11.3))
        rows.append((11.3, 11.4, 11.2, 11.25))
        rows.append((10.8, 10.85, 10.4, 10.5))
        result = _analyze(rows)
        self.assertTrue(result["island_top"])
        self.assertIn("岛形反转（顶）", result["pattern"])


class MultiFlagHygieneTests(unittest.TestCase):
    def test_all_multi_flags_exist_in_blank(self):
        keys = ("rising_three_methods", "falling_three_methods", "triple_top", "triple_bottom",
                "head_shoulders_top", "head_shoulders_bottom", "rounding_top", "rounding_bottom",
                "island_top", "island_bottom")
        for key in keys:
            self.assertIn(key, candles.blank())

    def test_early_return_keeps_the_same_keys(self):
        bars = _series([(10.0, 10.2, 9.8, 10.0)])
        self.assertEqual(sorted(candles.analyze(bars, 0)), sorted(candles._RESULT_KEYS))

    def test_context_is_optional_and_equivalent(self):
        """传不传预计算的摆动点，结论必须一模一样（那只是个加速手段）。"""
        bars = _series(_zigzag([9.5, 11.0, 10.3, 11.0, 10.35, 11.0, 10.6, 10.35, 9.9]))
        plain = candles.analyze(bars, len(bars) - 1)
        fast = candles.analyze(bars, len(bars) - 1, None, candles.context(bars)["graphic"])
        self.assertEqual(plain["pattern"], fast["pattern"])


if __name__ == "__main__":
    unittest.main()
