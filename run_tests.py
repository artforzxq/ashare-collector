#!/usr/bin/env python
"""测试入口：python run_tests.py [文件或测试名 ...] [-v]

**默认只打印失败详情和最后一行汇总。** 为什么要这样：五百多个测试的逐条输出有 700 多行，
其中 99% 是 "ok"——在终端里翻不到重点，如果由 AI 助手执行还会白白吃掉两万 token 左右的上下文。
失败的信息一条都不会少（unittest 无论如何都会打印失败用例的 traceback）。

要看逐条就跑 `-v`；只想跑一部分就在后面给名字，调试时快得多：

    python run_tests.py                            # 全跑，只报失败 + 汇总
    python run_tests.py -v                         # 全跑，逐条打印
    python run_tests.py tests/test_risk.py         # 只跑一个文件
    python run_tests.py tests.test_risk            # 只跑一个模块（两种写法都认）
    python run_tests.py tests.test_risk.RiskAssessTests   # 只跑一个类
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _as_target(name: str) -> str:
    """把 tests/test_x.py 这种写法转成 unittest 认的 tests.test_x。"""
    text = name.strip()
    if text.endswith(".py") or "/" in text or "\\" in text:
        path = Path(text)
        if path.suffix == ".py":
            path = path.with_suffix("")
        return ".".join(path.parts)
    return text


def main(argv: list[str]) -> int:
    args = [item for item in argv if item not in ("-v", "--verbose")]
    verbose = len(args) != len(argv)
    loader = unittest.defaultTestLoader
    if args:
        suite = loader.loadTestsFromNames([_as_target(item) for item in args])
        if suite.countTestCases() == 0:
            print(f"没找到这些测试：{', '.join(args)}")
            return 1
    else:
        suite = loader.discover(str(PROJECT_ROOT / "tests"), top_level_dir=str(PROJECT_ROOT))
    result = unittest.TextTestRunner(verbosity=2 if verbose else 0).run(suite)
    if not result.wasSuccessful():
        print("")
        print("想只重跑失败的那一块，把文件路径给它就行，例如：")
        print("    python run_tests.py tests/test_risk.py")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
