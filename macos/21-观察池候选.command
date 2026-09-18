#!/bin/bash
# 双击运行：观察池候选清单——谁该进池子、谁该出来。
# 只读本地筛选结果，不会自动改观察池；想加票就把打印出来的那行 add 命令复制执行。
# Windows 端对应：windows/21-观察池候选.bat
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.." || exit 1
exec bash "$HERE/_mac-run.sh" candidates
