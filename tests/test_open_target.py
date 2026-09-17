"""打开生成结果：启动脚本必须保持纯 ASCII，中文路径统一从这里走。

cmd.exe 读含中文的 .bat 会错位（整个文件从那一行起变成"不是内部命令"），
所以 .bat / .command 里只写英文键名，真正的路径映射放在这里。
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from collector import cli


class OpenTargetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "字段说明.md").write_text("x", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_known_key_opens_the_file(self):
        with mock.patch.object(cli.platform, "system", return_value="Windows"), \
                mock.patch.object(cli.os, "startfile", create=True) as opener:
            ok, message = cli.open_target("dictionary", self.root)
        self.assertTrue(ok)
        opener.assert_called_once()
        self.assertIn("字段说明.md", message)

    def test_missing_file_is_reported_not_opened(self):
        with mock.patch.object(cli.platform, "system", return_value="Windows"), \
                mock.patch.object(cli.os, "startfile", create=True) as opener:
            ok, message = cli.open_target("backtest", self.root)
        self.assertFalse(ok)
        self.assertIn("还没生成", message)
        opener.assert_not_called()

    def test_unknown_key_is_rejected(self):
        ok, message = cli.open_target("nope", self.root)
        self.assertFalse(ok)
        self.assertIn("不知道", message)

    def test_every_key_is_a_project_relative_path(self):
        self.assertTrue(cli.OPEN_TARGETS)
        for key, relative in cli.OPEN_TARGETS.items():
            self.assertFalse(Path(relative).is_absolute(), key)


if __name__ == "__main__":
    unittest.main()
