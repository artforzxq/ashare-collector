"""K 线形态的"关键位置"口径：页面只画长在关键位置、且本身够明显的形态。

页面上每天都画形态等于没有形态——一年 250 根 K 线，随便哪根都能被说成
"十字星"或"长阳"。所以这一层定死两件事：
  1. 形态本身要够硬（`obvious`）：反转确认、吞没、跳空、放量长实体……
  2. 位置要是关键位置（`key_position`）：20 日区间边缘、破 20 日高/低、均线穿越、20 日极值分型。
两个条件同时满足才画。位置判定只用当日及之前的数据，不引未来函数。
"""

import unittest

from collector import candles


def _bar(day: str, open_, high, low, close, volume: float = 1_000_000.0) -> dict:
    return {
        "code": "SH600000",
        "trade_date": day,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": volume * close,
    }


def _series(rows: list[tuple]) -> list[dict]:
    return [_bar(f"2026-01-{index + 1:02d}", *row) for index, row in enumerate(rows)]


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


class ObviousPatternTests(unittest.TestCase):
    def test_doji_is_not_obvious(self):
        self.assertFalse(candles.obvious({"pattern": "十字星", "volume_x": 1.0}))

    def test_two_signal_reversal_is_obvious(self):
        self.assertTrue(candles.obvious({"pattern": "长阳、阳包阴", "reversal_up": True}))

    def test_big_body_needs_volume_to_count(self):
        calm = {"pattern": "长阳", "long_bull": True, "body_pct": 4.0, "volume_x": 1.0}
        loud = {"pattern": "放量长阳", "long_bull": True, "body_pct": 4.0, "volume_x": 1.8}
        self.assertFalse(candles.obvious(calm))
        self.assertTrue(candles.obvious(loud))

    def test_gap_counts_on_its_own(self):
        self.assertTrue(candles.obvious({"pattern": "向上跳空", "gap_up": True, "volume_x": 1.0}))

    def test_bearish_twins_count_too(self):
        """看跌那一半（射击之星 / 阴包阳 / 向下跳空）必须和看涨的对称。

        踩过的坑：`obvious()` 里判断了这几个标志位，但 `analyze()` 的返回值里根本没这几个键
        ——查出来永远是 None，于是"单个看跌形态"一条都标不出来，只有凑成"反转确认"
        （≥2 个信号）才显示。这是测试该兜住的那类静默失效。
        """
        self.assertTrue(candles.obvious({"pattern": "长上影（射击之星）", "shooting": True, "volume_x": 1.4}))
        self.assertTrue(candles.obvious({"pattern": "阴包阳", "bearish_engulf": True, "volume_x": 1.3}))
        self.assertTrue(candles.obvious({"pattern": "向下跳空", "gap_down": True, "volume_x": 1.0}))


class AnalyzeFlagTests(unittest.TestCase):
    """analyze() 的返回值本身：标志位要齐全、不能自相矛盾。"""

    def _bars(self, *rows) -> list[dict]:
        return _series(list(rows))

    def test_one_word_limit_line_is_not_a_doji(self):
        """一字板没有振幅，不能按比例套形态——以前会被算成"十字星"，含义正好相反。"""
        bars = self._bars(*([(10.0, 10.2, 9.8, 10.0)] * 20), (11.0, 11.0, 11.0, 11.0))
        result = candles.analyze(bars, len(bars) - 1)
        self.assertIn("一字线", result["pattern"])
        self.assertFalse(result.get("doji"))
        self.assertFalse(result["reversal_up"] or result["reversal_down"])
        self.assertFalse(candles.obvious(result))
        self.assertTrue(result["zero_range"])

    def test_shooting_star_flag_is_returned(self):
        bars = self._bars((10.0, 10.2, 9.9, 10.0), (10.0, 10.2, 9.9, 10.0), (10.0, 11.0, 9.9, 9.95))
        result = candles.analyze(bars, 2)
        self.assertTrue(result["shooting"])
        self.assertIn("射击之星", result["pattern"])

    def test_bearish_engulf_flag_is_returned(self):
        bars = self._bars((10.0, 10.2, 9.9, 10.0), (9.5, 10.1, 9.4, 10.0), (10.1, 10.2, 9.2, 9.3))
        result = candles.analyze(bars, 2)
        self.assertTrue(result["bearish_engulf"])
        self.assertIn("阴包阳", result["pattern"])

    def test_gap_down_flag_is_returned(self):
        bars = self._bars((10.0, 10.2, 9.9, 10.0), (10.0, 10.5, 9.9, 10.4), (9.8, 9.85, 9.7, 9.75))
        result = candles.analyze(bars, 2)
        self.assertTrue(result["gap_down"])
        self.assertIn("向下跳空", result["pattern"])


class MarksTests(unittest.TestCase):
    def test_marks_only_keep_obvious_patterns_at_key_positions(self):
        flat = [(10.0, 10.2, 9.8, 10.0)] * 20
        # 一根阴线砸到区间下沿（关键位置），后面一根小阳线在中间（非关键位置）
        bars = _series(flat + [(9.85, 9.95, 9.55, 9.6), (9.6, 9.85, 9.5, 9.8)])
        found = candles.marks(bars)
        self.assertEqual([item["trade_date"] for item in found], ["2026-01-21"])
        self.assertIn("破 20 日低", found[0]["reasons"])
        self.assertTrue(found[0]["down"])

    def test_repeated_same_direction_within_three_bars_is_merged(self):
        """连着三根同方向的形态不是三个信号，是一个信号被抄了三遍。"""
        flat = [(10.0, 10.2, 9.8, 10.0)] * 20
        drop = [(9.85, 9.95, 9.55, 9.6), (9.6, 9.65, 9.2, 9.25)]
        found = candles.marks(_series(flat + drop))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["trade_date"], "2026-01-21")

    def test_marks_carry_the_reason_for_the_page(self):
        flat = [(10.0, 10.2, 9.8, 10.0)] * 20
        bars = _series(flat + [(9.85, 9.95, 9.55, 9.6)])
        item = candles.marks(bars)[0]
        for key in ("trade_date", "pattern", "up", "down", "reasons", "vol_x"):
            self.assertIn(key, item)


if __name__ == "__main__":
    unittest.main()
