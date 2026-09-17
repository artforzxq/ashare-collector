#!/bin/bash
# 双击运行：准备运行环境（每台电脑第一次做一次）。Windows 端对应：0-安装环境.bat
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.." || exit 1
exec bash "$HERE/_mac-run.sh" setup
