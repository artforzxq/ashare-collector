#!/bin/bash
# 双击运行：环境体检——Python / 依赖 / 数据库 / 配置，一次看全。只读，不改任何东西。
# Windows 端对应：windows/25-环境体检.bat
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.." || exit 1
exec bash "$HERE/_mac-run.sh" doctor
