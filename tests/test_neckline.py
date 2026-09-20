"""颈线：形态层要把它带出来（以前只是个局部变量，页面因此画不出来）。"""

import unittest

from collector import candles, levels


def _zigzag_shoulders_bottom():
    """造一个头肩底：三个谷（中间的更低）+ 两个峰之间是一段上涨，最后站上颈线。

    形状：左肩 10 → 峰 12 → 头 8 → 峰 12 → 右肩 10 → 站上 12。
    颈线应该是两个峰里较高的那个 = 12。
    前面多铺 6 根横盘：形态层的图形识别有 `peak_min_bars = 24` 的门槛，
    序列太短它会直接早退（这是故意的——刚上市几十根 K 线谈不上"图形形态"）。
    """
    prices = ([10.0] * 6                                        # 铺垫，凑够 peak_min_bars
              + [10.0] * 4 + [11.0, 12.0, 11.5, 10.5]          # 左肩
              + [10.0, 9.0, 8.0, 9.0, 10.0]                  # 头（更低）
              + [11.0, 12.0, 11.5, 10.5]                     # 右肩
              + [10.5, 10.8, 11.2, 11.8, 12.4, 12.6])        # 站上颈线
    bars = []
    for index, close in enumerate(prices):
        bars.append({
            "trade_date": f"2026-01-{index + 1:02d}",
            "open": close, "high": close * 1.004, "low": close * 0.996,
            "close": close, "volume": 1000, "amount": 1e8,
        })
    return bars


def _double_reading():
    """顶和底两套读法同时成立：三个峰 + 三个谷都在窗口里（SZ000333 的真实形状）。

    顶部读法：3 个峰 87.4 / 88.5 / 88.1，峰间两个谷 85.4 / 84.44 → 颈线 84.44（刚被跌破）
    底部读法：3 个谷 82.97 / 82.39 / 84.44，谷间两个峰 86.12 / 88.5 → 颈线 88.5
    后算的底部会把先算的顶部覆盖掉，于是图上画出一条在价格上方的"颈线"——
    真正成立的是三重顶，颈线 84.44。这里要保证取对。
    """
    # 锯齿走势，每段 4 根，摆动点落在段末（左右各 3 根都能确认）。
    # 数值照抄 SZ000333 的真实形状：三个峰 87.40 / 88.50 / 88.10，
    # 峰间两个**浅**谷 85.40 / 84.44 —— 三重顶的关键就是谷要浅，
    # 谷太深说明那不是"三个顶"，是别的形态。
    # 末尾停在下落途中（84.40，跌破 84.44），不再造出新的确认低点：
    # 和真实情况一样，最后一个已确认的摆动点是那个 88.10 的峰。
    legs = [[85.0, 86.0, 87.0, 87.40],      # 峰1 87.40
            [87.0, 86.5, 86.0, 85.40],      # 谷1 85.40（浅）
            [86.0, 87.0, 88.0, 88.50],      # 峰2 88.50
            [87.0, 86.0, 85.0, 84.44],      # 谷2 84.44
            [85.0, 86.0, 87.0, 88.10],      # 峰3 88.10
            [87.0, 86.0, 85.0, 84.20]]      # 回落，跌破颈线（真实是 84.40，这里留点余量）
    prices: list[float] = [83.5] * 6        # 铺垫，凑够 peak_min_bars
    for leg in legs:
        prices += leg
    bars = []
    for index, close in enumerate(prices):
        bars.append({
            "trade_date": f"2026-{(index // 28) + 1:02d}-{(index % 28) + 1:02d}",
            "open": close, "high": close * 1.001, "low": close * 0.999,
            "close": close, "volume": 1000, "amount": 1e8,
        })
    return bars


class NecklinePickingTests(unittest.TestCase):
    """顶/底两套读法同时成立时，只能认真正成立的那一种。"""

    def test_prefers_the_confirmed_top_pattern(self):
        bars = _double_reading()
        result = candles.analyze(bars, len(bars) - 1)
        self.assertEqual(result["neckline_kind"], "resistance")
        # 颈线取的是"峰间那个谷的**最低价**"，而合成 K 线的 low = close×0.999
        self.assertAlmostEqual(result["neckline"], 84.44 * 0.999, places=2)
        self.assertTrue(result["triple_top"] or result["head_shoulders_top"])

    def test_neckline_is_only_exposed_when_the_shape_holds(self):
        """没有形态的横盘不该有颈线（不能只要是"三个高点"就画一条线）。"""
        bars = [{"trade_date": f"2026-03-{index + 1:02d}", "open": 10.0, "high": 10.05,
                 "low": 9.95, "close": 10.0, "volume": 1000, "amount": 1e8}
                for index in range(40)]
        self.assertIsNone(candles.analyze(bars, len(bars) - 1)["neckline"])

    def test_band_carries_the_kind_for_the_page(self):
        """engine 列要带上"顶/底"，页面才能标"颈线（顶）/ 颈线（底）"。"""
        band = levels.neckline_band(_double_reading())
        self.assertIsNotNone(band)
        self.assertEqual(band["engine"], "pattern_resistance")

    def test_far_away_neckline_is_skipped(self):
        """离现价太远的颈线不画：页面会拿带子定纵轴范围，一条 20% 外的线会把 K 线压扁。"""
        # 这个夹具：颈线 12.0，最后收在 12.6 —— 差 5%
        bars = _zigzag_shoulders_bottom()
        self.assertIsNone(levels.neckline_band(bars, cfg={"levels": {"neckline_max_gap_pct": 3.0}}))
        self.assertIsNotNone(levels.neckline_band(bars, cfg={"levels": {"neckline_max_gap_pct": 15.0}}))


class NecklineTests(unittest.TestCase):
    def test_head_shoulders_bottom_exposes_a_support_neckline(self):
        bars = _zigzag_shoulders_bottom()
        # 逐个位置扫：站上颈线那一根（以及之后的），都应该带着颈线
        found = None
        for index in range(3, len(bars)):
            result = candles.analyze(bars, index)
            if result.get("neckline"):
                found = result
                break
        self.assertIsNotNone(found, "头肩底没有带出颈线")
        self.assertEqual(found["neckline_kind"], "support")
        self.assertAlmostEqual(found["neckline"], 12.0, places=1)

    def test_blank_result_still_has_the_keys(self):
        """键必须在：少一个键，调用方 .get() 拿到 None 就静默失效（踩过）。"""
        result = candles.blank()
        self.assertIn("neckline", result)
        self.assertIn("neckline_kind", result)
        self.assertIsNone(result["neckline"])
        self.assertEqual(result["neckline_kind"], "")

    def test_no_shape_means_no_neckline(self):
        bars = [{"trade_date": f"2026-02-{index + 1:02d}", "open": 10.0, "high": 10.1,
                 "low": 9.9, "close": 10.0, "volume": 1000, "amount": 1e8}
                for index in range(40)]
        self.assertIsNone(candles.analyze(bars, len(bars) - 1)["neckline"])

    def test_neckline_band_is_a_zero_width_band(self):
        """颈线进 levels 表时是一条"零宽带"：下游（画图、取带）一行都不用改。"""
        bars = _zigzag_shoulders_bottom()
        band = levels.neckline_band(bars)
        self.assertIsNotNone(band)
        self.assertEqual(band["level_type"], "neckline")
        self.assertEqual(band["price_low"], band["price_high"])
        self.assertAlmostEqual(band["price_low"], 12.0, places=1)

    def test_neckline_is_not_used_as_a_stop(self):
        """风险层按 level_type 取支撑带，颈线不在其中——止损逻辑一个字没动。"""
        bands = [{"level_type": "neckline", "price_low": 95.0, "price_high": 95.0}]
        self.assertIsNone(levels.support_for(bands, 100.0))


if __name__ == "__main__":
    unittest.main()
