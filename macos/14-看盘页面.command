#!/bin/bash
# 双击运行：打开看盘页面（K 线 / 指标 / 自选）。Windows 端对应：14-看盘页面.bat
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.." || exit 1
exec bash "$HERE/_mac-run.sh" web
