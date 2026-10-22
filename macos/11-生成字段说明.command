#!/bin/bash
# 双击运行：生成字段说明文档。Windows 端对应：11-生成字段说明.bat
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.." || exit 1
exec bash "$HERE/_mac-run.sh" dictionary
