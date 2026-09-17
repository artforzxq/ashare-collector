#!/bin/bash
# 双击运行：抓数据 → 算状态 → 出提醒。Windows 端对应：3-每日任务.bat
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.." || exit 1
exec bash "$HERE/_mac-run.sh" daily
