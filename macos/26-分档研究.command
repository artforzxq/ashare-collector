#!/bin/bash
# 双击运行：分档研究——把全市场按某个维度分档，看每档之后 20 天的表现。
# 用来验证"某个想法到底有没有用"，比靠印象靠谱。
# Windows 端对应：windows/26-分档研究.bat
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.." || exit 1
exec bash "$HERE/_mac-run.sh" buckets
