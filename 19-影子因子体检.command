#!/bin/bash
# 双击运行：因子台账体检（影子因子能不能转正）。Windows 端对应：19-影子因子体检.bat
cd "$(dirname "$0")" || exit 1
exec bash _mac-run.sh shadow
