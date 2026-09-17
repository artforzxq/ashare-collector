#!/bin/bash
# 双击运行：在桌面放一个「每日任务」双击即跑。Windows 端对应：10-创建桌面快捷方式.bat
cd "$(dirname "$0")" || exit 1
exec bash _mac-run.sh shortcut
