#!/bin/bash
# 双击运行：回填提醒的实际表现，统计胜率。Windows 端对应：16-信号复盘.bat
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.." || exit 1
exec bash "$HERE/_mac-run.sh" review
