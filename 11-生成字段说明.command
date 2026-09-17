#!/bin/bash
# 双击运行：生成字段说明文档。Windows 端对应：11-生成字段说明.bat
cd "$(dirname "$0")" || exit 1
exec bash _mac-run.sh dictionary
