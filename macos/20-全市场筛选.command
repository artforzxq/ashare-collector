#!/bin/bash
# 双击运行：扫本地全部标的，按形态分组。Windows 端对应：windows/20-全市场筛选.bat
# 要先把 18-全市场同步 跑一跑，本地有多少只票就扫多少只。
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.." || exit 1
exec bash "$HERE/_mac-run.sh" screen
