#!/bin/bash
# 双击运行：离线自检，验证环境。Windows 端对应：2-自检.bat
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.." || exit 1
exec bash "$HERE/_mac-run.sh" selftest
