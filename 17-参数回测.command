#!/bin/bash
# 双击运行：扫状态机参数，看哪组真的有效。Windows 端对应：17-参数回测.bat
cd "$(dirname "$0")" || exit 1
exec bash _mac-run.sh backtest
