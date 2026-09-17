#!/bin/bash
# 双击运行：列出所有表并生成浏览页面。（Windows 的 8-数据库工具 在 mac 上由本文件替代）
cd "$(dirname "$0")" || exit 1
exec bash _mac-run.sh tables
