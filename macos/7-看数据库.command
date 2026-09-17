#!/bin/bash
# 双击运行：列出所有表并生成浏览页面。（Windows 的 8-数据库工具 在 mac 上由本文件替代）
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.." || exit 1
exec bash "$HERE/_mac-run.sh" tables
