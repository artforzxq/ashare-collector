#!/bin/bash
# 双击运行：抓数据 → 算状态 → 出提醒。Windows 端对应：3-每日任务.bat
cd "$(dirname "$0")" || exit 1
exec bash _mac-run.sh daily
