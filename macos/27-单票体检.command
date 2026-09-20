#!/bin/bash
# 双击运行：单票体检——把这只票在各层里的说法（状态/因子/位置/形态/关键带/风险/提醒/筛选历史）
# 按决策链路的顺序摆到一张纸上，最后加一段"它在全市场分档里落在哪一格"。
# Windows 端对应：windows/27-单票体检.bat
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.." || exit 1
exec bash "$HERE/_mac-run.sh" dig
