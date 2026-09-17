#!/bin/bash
# 双击运行：打开看盘页面（K 线 / 指标 / 自选）。Windows 端对应：14-看盘页面.bat
cd "$(dirname "$0")" || exit 1
exec bash _mac-run.sh web
