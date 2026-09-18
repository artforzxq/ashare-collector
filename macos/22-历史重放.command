#!/bin/bash
# 双击运行：把筛选条件放到过去每一天重跑一遍，立刻得到每个形态真实的 5 / 20 日表现。
# 为什么要这么做：今天筛出来的票要等下个月才知道后来怎么样，但历史数据已经在本地了。
# Windows 端对应：windows/22-历史重放.bat
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.." || exit 1
exec bash "$HERE/_mac-run.sh" replay --days 240
