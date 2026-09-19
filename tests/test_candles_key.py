"""K 线形态层：几何原语、趋势前提、关键位置、组合形态。

写法上刻意分三类用例（正例 / 负例 / 边界），负例比正例重要：
   · 大实体 + 长下影**不是**锤子（缺少"小实体"）；
   · 下影不到实体 2 倍**不是**锤子；
   · 形状对但前面是上涨 → 那是**上吊线**，不是锤子（同形异位）；
   · 一字线没有振幅，不参与任何形态判定；
   · 横盘里随便两根"平"的低点**不是**平底，得平在 20 日极值上。
这套用例也是 `THRESHOLDS` 改动的回归网——调阈值先看这里红不红。
"""

import unittest

from collector import candles


def _bar(day: str, open_, high, low, close, volume: float = 1_000_000.0) -> dict:
    return {
        "code": "SH600000", "trade_date": day, "open": open_, "high": high,
        "low": low, "close": close, "volume": volume, "amount": volume * close,
    }


def _series(rows: list[tuple]) -> list[dict]:
    return [_bar(f"2026-{index // 28 + 1:02d}-{index % 28 + 1:02d}", *row) for index, row in enumerate(rows)]


def _leg(closes: list[float]) -> list[tuple]:
    """把一串收盘价连成 K 线（开盘=前收，影线各留一点）。"""
    rows: list[tuple] = []
    previous = None
    for close in closes:
        open_ = previous if previous is not None else close * 1.01
        rows.append((open_, max(open_, close) * 1.002, min(open_, close) * 0.998, close))
        previous = close
    return rows


def _down(n: int = 12, start: float = 12.0, step: float = 0.97) -> list[tuple]:
    return _leg([round(start * step ** i, 3) for i in range(n)])


def _up(n: int = 12, start: float = 10.0, step: float = 1.03) -> list[tuple]:
    return _leg([round(start * step ** i, 3) for i in range(n)])


def _flat(n: int = 20, price: float = 9.5, low: float = 9.0) -> list[tuple]:
    return [(price, price * 1.01, low, price) for _ in range(n)]


class BlankShapeTests(unittest.TestCase):
    """返回值形状：所有键都在，且"没有结论"必须是 None 而不是 False。"""

    def test_blank_has_every_key(self):
        self.assertEqual(sorted(candles.blank()), sorted(candles._RESULT_KEYS))

    def test_numeric_fields_are_none_not_false(self):
        """False 在 Python 里等于 0，筛选条件 `candle.body_pct >= 0` 会被空结论判成通过。"""
        for key in ("body_pct", "body_r", "volume_x", "close_pos", "trend"):
            self.assertIsNone(candles.blank()[key], key)

    def test_early_return_keeps_the_same_keys(self):
        short = _series([(10.0, 10.2, 9.8, 10.0)])
        self.assertEqual(sorted(candles.analyze(short, 0)), sorted(candles._RESULT_KEYS))


class KeyPositionTests(unittest.TestCase):
    def test_bottom_of_the_range_is_a_key_position(self):
        flat = [(10.0, 10.2, 9.8, 10.0)] * 20
        bars = _series(flat + [(9.9, 9.95, 9.82, 9.83)])
        spot = candles.key_position(bars, len(bars) - 1)
        self.assertTrue(spot["key"])
        self.assertIn("20 日区间下沿", spot["reasons"])

    def test_middle_of_the_range_is_not_a_key_position(self):
        """横盘中间的价格什么都不是——形态长在这里，说明不了任何事。"""
        flat = [(10.0, 10.1, 9.9, 10.0)] * 19
        bars = _series(flat + [(10.1, 14.0, 10.0, 14.0), (14.0, 11.7, 11.5, 11.6)])
        spot = candles.key_position(bars, len(bars) - 1)
        self.assertFalse(spot["key"])
        self.assertEqual(spot["reasons"], [])

    def test_break_of_the_20_day_low_is_a_key_position(self):
        flat = [(10.0, 10.2, 9.9, 10.0)] * 20
        bars = _series(flat + [(9.95, 10.0, 9.4, 9.5)])
        self.assertIn("破 20 日低", candles.key_position(bars, len(bars) - 1)["reasons"])

    def test_tiny_local_dip_is_not_a_fractal_key_position(self):
        """每三天就有一个局部小坑，那种"分型"不算关键位置，否则图上全是点。"""
        noisy = [(10.0, 10.6, 9.5 + (index % 3) * 0.5, 10.2) for index in range(20)]
        bars = _series(noisy + [(10.2, 10.3, 9.85, 10.0), (10.0, 10.4, 10.05, 10.2)])
        spot = candles.key_position(bars, len(bars) - 2)
        self.assertNotIn("20 日新低分型", spot["reasons"])

    def test_uses_no_future_data_for_range_checks(self):
        """区间位置只看当日之前——后面再涨多少，都不能改变"当时它在低位"这个事实。"""
        flat = [(10.0, 10.2, 9.8, 10.0)] * 20
        bars = _series(flat + [(9.9, 9.95, 9.82, 9.83)])
        position_only = lambda reasons: [item for item in reasons if "区间" in item or "破" in item]
        before = candles.key_position(bars, len(bars) - 1)
        later = candles.key_position(bars + _series([(20.0, 25.0, 19.0, 24.0)]), len(bars) - 1)
        self.assertEqual(position_only(before["reasons"]), position_only(later["reasons"]))


class TrendContextTests(unittest.TestCase):
    """同形异位：形状一样，位置（趋势背景）不一样，名字和方向就不一样。"""

    HAMMER = (10.0, 10.05, 9.0, 9.9)
    STAR = (10.0, 11.0, 9.95, 9.95)

    def test_hammer_needs_a_prior_decline(self):
        bars = _series(_down() + [self.HAMMER])
        result = candles.analyze(bars, len(bars) - 1)
        self.assertEqual(result["trend"], "down")
        self.assertTrue(result["hammer"])
        self.assertFalse(result["hanging_man"])
        self.assertIn("锤子", result["pattern"])

    def test_same_shape_after_a_rise_is_a_hanging_man(self):
        bars = _series(_up() + [self.HAMMER])
        result = candles.analyze(bars, len(bars) - 1)
        self.assertEqual(result["trend"], "up")
        self.assertTrue(result["hanging_man"])
        self.assertFalse(result["hammer"])
        self.assertIn("上吊线", result["pattern"])

    def test_shooting_star_needs_a_prior_rise(self):
        bars = _series(_up() + [self.STAR])
        result = candles.analyze(bars, len(bars) - 1)
        self.assertTrue(result["shooting"])
        self.assertIn("流星", result["pattern"])

    def test_same_shape_after_a_decline_is_an_inverted_hammer(self):
        bars = _series(_down() + [self.STAR])
        result = candles.analyze(bars, len(bars) - 1)
        self.assertTrue(result["inverted_hammer"])
        self.assertFalse(result["shooting"])
        self.assertIn("倒锤星", result["pattern"])

    def test_short_history_gives_no_trend_conclusion(self):
        bars = _series([(10.0, 10.2, 9.8, 10.0)] * 4 + [self.HAMMER])
        result = candles.analyze(bars, len(bars) - 1)
        self.assertEqual(result["trend"], "flat")
        self.assertFalse(result["hammer"])      # 趋势不明就不给"下跌后"的结论
        self.assertFalse(result["hanging_man"])


class SingleCandleEdgeTests(unittest.TestCase):
    def test_big_body_with_long_lower_shadow_is_not_a_hammer(self):
        """实体占了大半幅，那叫"长下影的大阴线"，不能算锤子（教科书要求小实体）。"""
        bars = _series(_down() + [(10.5, 10.55, 8.0, 9.3)])
        result = candles.analyze(bars, len(bars) - 1)
        self.assertGreater(result["body_r"], candles.THRESHOLDS["small_body_r"])
        self.assertFalse(result["hammer"])

    def test_shadow_shorter_than_two_bodies_is_not_a_hammer(self):
        bars = _series(_down() + [(10.0, 10.05, 9.6, 9.9)])
        result = candles.analyze(bars, len(bars) - 1)
        self.assertFalse(result["hammer"])

    def test_one_word_limit_line_is_not_a_doji(self):
        """一字板没有振幅，不能按比例套形态——以前会被算成"十字星"，含义正好相反。"""
        bars = _series([(10.0, 10.2, 9.8, 10.0)] * 20 + [(11.0, 11.0, 11.0, 11.0)])
        result = candles.analyze(bars, len(bars) - 1)
        self.assertIn("一字线", result["pattern"])
        self.assertTrue(result["zero_range"])
        self.assertFalse(result["doji"])
        self.assertFalse(candles.obvious(result))

    def test_bearish_single_patterns_are_returned(self):
        """看跌那一半必须和看涨对称：标志位存在才可能被标出来。"""
        bars = _series(_up() + [(10.0, 11.0, 9.95, 9.95)])
        self.assertTrue(candles.analyze(bars, len(bars) - 1)["shooting"])
        engulf = _series([(10.0, 10.2, 9.9, 10.0), (9.9, 10.1, 9.85, 10.05), (10.1, 10.2, 9.2, 9.3)])
        self.assertTrue(candles.analyze(engulf, 2)["bearish_engulf"])
        gap = _series([(10.0, 10.2, 9.9, 10.0), (10.0, 10.5, 9.9, 10.4), (9.8, 9.85, 9.7, 9.75)])
        self.assertTrue(candles.analyze(gap, 2)["gap_down"])


class ComboPatternTests(unittest.TestCase):
    """组合形态：每一条都要有正例，也要有"差一点"的负例。"""

    def _last(self, rows: list[tuple]) -> dict:
        bars = _series(rows)
        return candles.analyze(bars, len(bars) - 1)

    def test_morning_star(self):
        result = self._last(_down() + [
            (10.5, 10.55, 9.7, 9.75),      # 长阴
            (9.6, 9.65, 9.5, 9.55),        # 小实体，整体落在长阴之下
            (9.6, 10.45, 9.55, 10.4),      # 长阳，收回一半以上
        ])
        self.assertTrue(result["morning_star"])
        self.assertTrue(result["combo_up"])
        self.assertTrue(result["reversal_up"])         # 组合形态本身就是确认
        self.assertIn("早晨之星", result["pattern"])

    def test_morning_star_needs_a_prior_decline(self):
        """前面是涨的，"长阴→小实体→长阳"只是回调，不是底部反转。"""
        result = self._last(_up() + [
            (10.5, 10.55, 9.7, 9.75), (9.6, 9.65, 9.5, 9.55), (9.6, 10.45, 9.55, 10.4),
        ])
        self.assertFalse(result["morning_star"])

    def test_evening_star(self):
        result = self._last(_up() + [
            (9.5, 10.3, 9.45, 10.25),
            (10.35, 10.5, 10.3, 10.4),
            (10.35, 10.4, 9.55, 9.6),
        ])
        self.assertTrue(result["evening_star"])
        self.assertTrue(result["combo_down"])
        self.assertIn("黄昏之星", result["pattern"])

    def test_harami_bull(self):
        result = self._last(_down() + [
            (10.0, 10.05, 9.0, 9.05),      # 长阴
            (9.3, 9.8, 9.25, 9.7),         # 阳线，实体完全缩在里面
        ])
        self.assertTrue(result["harami_bull"])
        self.assertIn("看涨孕线", result["pattern"])

    def test_harami_bull_rejects_a_child_larger_than_the_parent(self):
        result = self._last(_down() + [
            (10.0, 10.05, 9.0, 9.05), (9.2, 10.0, 9.1, 9.9),
        ])
        self.assertFalse(result["harami_bull"])

    def test_harami_bear(self):
        result = self._last(_up() + [
            (9.0, 10.05, 8.95, 10.0), (9.7, 9.75, 9.55, 9.6),
        ])
        self.assertTrue(result["harami_bear"])
        self.assertIn("看跌孕线", result["pattern"])

    def test_dark_cloud_cover(self):
        result = self._last(_up() + [
            (10.0, 11.25, 9.95, 11.2), (11.3, 11.35, 10.5, 10.55),
        ])
        self.assertTrue(result["dark_cloud_cover"])
        self.assertIn("乌云盖顶", result["pattern"])

    def test_dark_cloud_cover_rejects_a_full_engulf(self):
        """完全吞掉母阳线那是"看跌吞没"，不是乌云盖顶（刺入深度必须过半但没吞掉）。"""
        result = self._last(_up() + [
            (10.0, 11.25, 9.95, 11.2), (11.3, 11.35, 9.8, 9.9),
        ])
        self.assertFalse(result["dark_cloud_cover"])

    def test_piercing(self):
        result = self._last(_down() + [
            (11.2, 11.25, 9.95, 10.0), (9.9, 10.7, 9.85, 10.65),
        ])
        self.assertTrue(result["piercing"])
        self.assertIn("刺透形态", result["pattern"])

    def test_tweezers_bottom(self):
        result = self._last(_flat() + [
            (9.5, 9.6, 9.0, 9.2), (9.2, 9.7, 9.0, 9.65),
        ])
        self.assertTrue(result["tweezers_bottom"])
        self.assertIn("平底", result["pattern"])

    def test_tweezers_away_from_the_extreme_do_not_count(self):
        """脱离 20 日极值的"平"没有含义——横盘里天天都有，不加这条每只票一年能触发 36 次。"""
        result = self._last(_flat(low=8.5) + [
            (9.5, 9.6, 9.4, 9.45), (9.45, 9.7, 9.4, 9.65),
        ])
        self.assertFalse(result["tweezers_bottom"])

    def test_tweezers_top(self):
        rows = [(9.5, 10.0, 9.4, 9.6) for _ in range(20)]
        result = self._last(rows + [(9.6, 10.0, 9.5, 9.8), (9.8, 10.0, 9.6, 9.65)])
        self.assertTrue(result["tweezers_top"])
        self.assertIn("平顶", result["pattern"])

    def test_three_white_soldiers(self):
        result = self._last(_down() + [
            (10.0, 10.6, 9.95, 10.55), (10.6, 11.25, 10.55, 11.2), (11.25, 11.9, 11.2, 11.85),
        ])
        self.assertTrue(result["three_white_soldiers"])
        self.assertIn("红三兵", result["pattern"])

    def test_three_black_crows(self):
        result = self._last(_up() + [
            (12.0, 12.05, 11.4, 11.45), (11.4, 11.45, 10.8, 10.85), (10.8, 10.85, 10.2, 10.25),
        ])
        self.assertTrue(result["three_black_crows"])
        self.assertIn("三只乌鸦", result["pattern"])

    def test_combo_flags_are_all_present(self):
        """组合形态的标志位也要全员在场（不然提醒层读到的是 None）。"""
        keys = ("morning_star", "evening_star", "three_white_soldiers", "three_black_crows",
                "harami_bull", "harami_bear", "dark_cloud_cover", "piercing",
                "tweezers_bottom", "tweezers_top")
        result = self._last(_flat() + [(9.5, 9.6, 9.0, 9.2), (9.2, 9.7, 9.0, 9.65)])
        for key in keys:
            self.assertIn(key, result)


class ObviousPatternTests(unittest.TestCase):
    def test_doji_is_not_obvious(self):
        self.assertFalse(candles.obvious({"pattern": "十字星", "volume_x": 1.0}))

    def test_combo_alone_is_obvious(self):
        self.assertTrue(candles.obvious({"pattern": "早晨之星", "combo_up": True}))

    def test_two_signal_reversal_is_obvious(self):
        self.assertTrue(candles.obvious({"pattern": "长阳、阳包阴", "reversal_up": True}))

    def test_big_body_needs_volume_to_count(self):
        calm = {"pattern": "长阳", "long_bull": True, "volume_x": 1.0}
        loud = {"pattern": "放量长阳", "long_bull": True, "volume_x": 1.8}
        self.assertFalse(candles.obvious(calm))
        self.assertTrue(candles.obvious(loud))

    def test_gap_counts_on_its_own(self):
        self.assertTrue(candles.obvious({"pattern": "向上跳空", "gap_up": True, "volume_x": 1.0}))

    def test_bearish_twins_count_too(self):
        self.assertTrue(candles.obvious({"pattern": "流星", "shooting": True, "volume_x": 1.4}))
        self.assertTrue(candles.obvious({"pattern": "阴包阳", "bearish_engulf": True, "volume_x": 1.3}))
        self.assertTrue(candles.obvious({"pattern": "向下跳空", "gap_down": True, "volume_x": 1.0}))


class MarksTests(unittest.TestCase):
    def test_marks_only_keep_obvious_patterns_at_key_positions(self):
        flat = [(10.0, 10.2, 9.8, 10.0)] * 20
        bars = _series(flat + [(9.85, 9.95, 9.55, 9.6), (9.6, 9.85, 9.5, 9.8)])
        found = candles.marks(bars)
        self.assertEqual([item["trade_date"] for item in found],
                         [bars[20]["trade_date"]])
        self.assertIn("破 20 日低", found[0]["reasons"])
        self.assertTrue(found[0]["down"])

    def test_repeated_same_direction_within_three_bars_is_merged(self):
        """连着三根同方向的形态不是三个信号，是一个信号被抄了三遍。"""
        flat = [(10.0, 10.2, 9.8, 10.0)] * 20
        drop = [(9.85, 9.95, 9.55, 9.6), (9.6, 9.65, 9.2, 9.25)]
        found = candles.marks(_series(flat + drop))
        self.assertEqual(len(found), 1)

    def test_marks_carry_the_reason_and_the_combo_flag(self):
        flat = [(9.5, 9.6, 9.0, 9.55) for _ in range(20)]
        bars = _series(flat + [(9.5, 9.6, 9.0, 9.2), (9.2, 9.7, 9.0, 9.65)])
        item = candles.marks(bars)[-1]
        for key in ("trade_date", "pattern", "up", "down", "combo", "reasons", "volume_x"):
            self.assertIn(key, item)
        self.assertTrue(item["combo"])


class DescribeTests(unittest.TestCase):
    def test_describe_carries_position_and_confirmation(self):
        flat = [(9.5, 9.6, 9.0, 9.55) for _ in range(20)]
        bars = _series(flat + [(9.5, 9.6, 9.0, 9.2), (9.2, 9.7, 9.0, 9.65)])
        text = candles.describe(candles.analyze(bars, len(bars) - 1))
        self.assertIn("关键位置", text)
        self.assertIn("平底", text)

    def test_describe_empty_result_says_so(self):
        self.assertEqual(candles.describe(candles.blank()), "无显著形态")


if __name__ == "__main__":
    unittest.main()
