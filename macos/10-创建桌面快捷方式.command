#!/bin/bash
# 双击运行：在桌面放一个「每日任务」双击即跑。Windows 端对应：10-创建桌面快捷方式.bat
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.." || exit 1
exec bash "$HERE/_mac-run.sh" shortcut
