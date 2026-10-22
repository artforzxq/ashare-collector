#!/bin/bash
# 双击运行：把全市场历史分批补到本地（可反复点，已经跟到最新交易日的会自动跳过）。
# Windows 端对应：windows/18-全市场同步.bat
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.." || exit 1
exec bash "$HERE/_mac-run.sh" sync
