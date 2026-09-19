#!/bin/bash
# 双击运行：把当天简报推到手机（PushPlus）。Windows 端对应：23-推送简报.bat
# 想发一条测试消息：终端里跑 bash macos/_mac-run.sh push --test
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.." || exit 1
exec bash "$HERE/_mac-run.sh" push
