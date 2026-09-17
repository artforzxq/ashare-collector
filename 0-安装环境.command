#!/bin/bash
# 双击运行：准备运行环境（每台电脑第一次做一次）。Windows 端对应：0-安装环境.bat
cd "$(dirname "$0")" || exit 1
exec bash _mac-run.sh setup
