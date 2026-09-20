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
