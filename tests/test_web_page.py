"""看盘页面的静态自检：元素 id、类选择器、外部资源。

页面是手写的 HTML/JS，没有构建步骤也没有浏览器测试，所以这类"改了模板忘了改选择器"
的低级 bug 只能靠静态检查兜住——之前就因为把 .code 改名成 .nm，导致点自选列表没反应。
"""

import re
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parents[1] / "collector" / "web" / "index.html"


class PageStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = PAGE.read_text(encoding="utf-8")
        cls.body, _, cls.script = cls.html.partition("<script>")

    def test_ids_referenced_by_js_exist(self):
        defined = set(re.findall(r'id="([^"]+)"', self.body))
        used = set(re.findall(r"\$\('([^']+)'\)", self.script))
        self.assertEqual(sorted(used - defined), [], "JS 引用了不存在的 id（会报 null 然后静默失效）")

    def test_class_selectors_used_by_js_exist_in_markup(self):
        used = set(re.findall(r"querySelector(?:All)?\('\.([a-zA-Z0-9_-]+)", self.script))
        defined = set(re.findall(r'class="([^"]+)"', self.html))
        flattened = {name for group in defined for name in group.split()}
        flattened |= set(re.findall(r"\.([a-zA-Z0-9_-]+)\s*\{", self.html))     # CSS 里定义的
        missing = sorted(used - flattened)
        self.assertEqual(missing, [], f"JS 用了不存在的类选择器：{missing}")

    def test_template_classes_are_covered(self):
        """模板字符串里写的 class 也要能被选择器找到（比如 wl-item)."""
        for name in ("wl-item", "screen", "results"):
            self.assertIn(name, self.html)

    def test_no_external_resources(self):
        self.assertNotRegex(self.html, r'(src|href)="https?://')

    def test_help_panel_wired(self):
        """指标说明面板必须真的能打开（不然用户点了个寂寞）。"""
        for anchor in ("helpPanel", "helpBtn", "helpBody"):
            self.assertIn(anchor, self.html)

    def test_every_help_entry_is_rendered(self):
        """buildHelp 里写了的条目，renderHelpPanel 里必须列出来。

        踩过的坑：加了「换手放量折价」的说明，但面板是**按固定顺序**渲染的，
        忘了加进那个数组，于是定义写了、页面上看不到——静态检查能兜住这类漏。
        """
        script = self.script
        defined = set(re.findall(r"^\s{4}([a-z_]+):\s*\{", script, re.M))
        order = re.search(r"const rows = \[([^\]]+)\]\.map", script)
        self.assertIsNotNone(order, "renderHelpPanel 的渲染顺序丢了？")
        rendered = set(re.findall(r"'([a-z_]+)'", order.group(1)))
        self.assertTrue(defined, "没解析到任何说明条目，检查 buildHelp 的缩进")
        self.assertEqual(sorted(defined - rendered), [],
                         "这些说明写了但没渲染出来：见 renderHelpPanel 的数组")


if __name__ == "__main__":
    unittest.main()
