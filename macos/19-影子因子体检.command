#!/bin/bash
# 双击运行：因子台账体检（影子因子能不能转正）。Windows 端对应：19-影子因子体检.bat
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.." || exit 1
exec bash "$HERE/_mac-run.sh" shadow
