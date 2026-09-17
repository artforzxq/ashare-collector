#!/usr/bin/env python
"""命令行入口。

嵌入式 Python（python3xx._pth）不会把当前目录放进模块搜索路径，
所以统一用这个脚本启动，而不是 `python -m collector.cli`。

用法：
    python run.py init-db
    python run.py sources
    python run.py daily
    python run.py report
    python run.py factors SH000300
    python run.py selftest
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Windows 控制台默认不是 UTF-8，中文会显示成乱码，这里做一次无害修正
if os.name == "nt":
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from collector.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
