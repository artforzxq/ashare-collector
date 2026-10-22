#!/bin/bash
# 双击运行：列出所有表并生成浏览页面。Windows 端对应：windows/7-看数据库.bat
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.." || exit 1
exec bash "$HERE/_mac-run.sh" tables
