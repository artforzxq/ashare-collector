#!/bin/bash
# 双击运行：扫状态机参数，看哪组真的有效。Windows 端对应：17-参数回测.bat
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.." || exit 1
exec bash "$HERE/_mac-run.sh" backtest
