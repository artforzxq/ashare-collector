"""分享图生成测试：SVG 结构、卡片内容、渲染兜底（都不联网、不开浏览器）。"""

import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from collector import server, share
from collector.config import load_config
from tests.test_server import CONFIG_SAMPLE, _seed_db


def _bars(count: int = 40) -> list[dict]:
    rows = []
    price = 100.0
    for index in range(count):
        price = price * (1 + (0.004 if index % 3 else -0.003))
        rows.append({
            "trade_date": f"2026-08-{index + 1:02d}",
            "open": price * 0.995, "high": price * 1.01, "low": price * 0.99,
            "close": price, "volume": 1e7 + index * 1e5, "amount": price * 1e7,
            "pct_chg": 0.4, "ma20": price * 0.98, "ma60": None, "ma120": None,
            "trend_score": 55.0, "state": "range", "state_days": 3,
            "opportunity_score": 0.5, "vol_ratio_20": 1.1,
        })
    return rows


class ChartSvgTests(unittest.TestCase):
    def test_svg_is_wellformed_and_within_canvas(self):
        bars = _bars(40)
        levels = [{"level_type": "support", "price_low": 95.0, "price_high": 96.0, "weight": 1}]
        alerts = [{"trade_date": bars[-1]["trade_date"], "level": "P1"}]
        svg = share.svg_chart(bars, levels, alerts, width=1000, height=620)
        root = ET.fromstring(svg)                      # 结构不合法这里就会抛
        self.assertEqual(root.get("width"), "1000")
        self.assertEqual(len(root.findall(".//{http://www.w3.org/2000/svg}rect")), len(bars) * 2 + len(levels))
        self.assertEqual(len(root.findall(".//{http://www.w3.org/2000/svg}polyline")), 1)   # 只有 ma20 有值
        # 只查数值属性，别把合法的 fill="none" 当成坏值
        self.assertNotRegex(svg.lower(), r'(?:x|y|cx|cy|r|width|height|points)="[^"]*(?:nan|inf|none)[^"]*"')
        for value in re.findall(r'y="(-?[\d.]+)"', svg):
            self.assertLessEqual(float(value), 621)
            self.assertGreaterEqual(float(value), -1)

    def test_empty_bars_still_returns_svg(self):
        self.assertIn("<svg", share.svg_chart([], [], []))


class CardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cfg_path = root / "config.yaml"
        cfg_path.write_text(CONFIG_SAMPLE, encoding="utf-8")
        cls.cfg = load_config(cfg_path, project_root=root)
        cls.cfg["_db_path"] = str(root / "market.db")
        cls.cfg["_config_path"] = str(cfg_path)
        _seed_db(Path(cls.cfg["_db_path"]))
        cls.app = server.App(cls.cfg)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_card_content_and_no_external_resources(self):
        card = share.build_card(self.app, "SH000300", days=30)
        self.assertIn("<h1>沪深300</h1>", card)         # 中文名来自 names.py
        self.assertIn('class="price">4480.00<', card)   # 种子库里最后一根的收盘价
        self.assertIn("震荡", card)
        self.assertIn("关键带", card)
        self.assertIn("仅供参考", card)
        self.assertIn(f"height:{share.CARD_HEIGHT}px", card)
        # 不能有任何外部资源，否则朋友那边断网/被墙就白了
        self.assertNotRegex(card, r'(src|href)="https?://')

    def test_generate_falls_back_to_html_when_no_renderer(self):
        original = share.render_png
        share.render_png = lambda html_path, png_path: ""      # 模拟机器上没有浏览器
        try:
            result = share.generate(self.cfg, codes=["SH000300"], days=30,
                                    out_dir=Path(self.tmp.name) / "out", verbose=False)
        finally:
            share.render_png = original
        self.assertTrue(result["ok"])
        self.assertEqual(result["renderer"], "")
        self.assertEqual(len(result["images"]), 1)
        self.assertEqual(result["images"][0].suffix, ".html")
        self.assertTrue(result["images"][0].exists())

    def test_generate_skips_instruments_without_data(self):
        result = share.generate(self.cfg, codes=["SH999999"], days=30,
                                out_dir=Path(self.tmp.name) / "out", verbose=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["skipped"], ["SH999999"])


if __name__ == "__main__":
    unittest.main()
