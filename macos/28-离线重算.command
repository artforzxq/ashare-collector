#!/bin/bash
# 双击运行：离线重算——只用本地日线重算特征 / 关键带 / 风险 / 提醒，完全不联网。
# 什么时候用：日线已经补进来了（比如刚跑完全市场同步），但页面上特征还是旧日期、结论全是横线。
# 收盘前也能安全跑：它不会去抓今天那根半根日线。
# Windows 端对应：windows/28-离线重算.bat
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.." || exit 1
exec bash "$HERE/_mac-run.sh" daily --offline
