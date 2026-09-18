#!/bin/bash
# ============================================================
#  共享启动器（macOS 端）。Windows 端对应文件：win-run.bat
#  用法：bash _mac-run.sh <步骤> [附加参数]
#  步骤：setup init-db selftest daily report sources dashboard
#        tables sql dictionary rebuild auto install-sources shortcut
#
#  运行环境默认放在 ~/ashare-env，刻意不放进项目文件夹：
#  虚拟环境里有几百兆的 mac 专用文件，同步到 Windows 上只会变成垃圾。
#  想换位置：export ASHARE_VENV=/你的/路径
# ============================================================

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.." || exit 1

VENV="${ASHARE_VENV:-$HOME/ashare-env}"
PYNOTE="$HOME/ashare-python.txt"
PY="$VENV/bin/python"
STEP="${1:-}"
shift 2>/dev/null || true

pause() {
  [ "${ASHARE_NO_PAUSE:-}" = "1" ] && return 0
  printf "\n按回车键关闭窗口… "
  read -r _ || true
}

setup_python() {
  echo "=== 准备运行环境（第一次约 1-2 分钟）==="
  if ! command -v python3 >/dev/null 2>&1; then
    echo "没找到 python3。请先装 Python 3.10 以上版本，任选一种："
    echo "  1) 打开 https://www.python.org/downloads/ 下载安装"
    echo "  2) 终端执行：brew install python"
    return 1
  fi
  echo "用 $(python3 --version 2>&1) 创建环境：$VENV"
  python3 -m venv "$VENV" || { echo "创建环境失败。"; return 1; }
  "$VENV/bin/python" -m pip install --quiet --upgrade pip
  "$VENV/bin/python" -m pip install --quiet pyyaml baostock akshare || {
    echo "安装依赖失败，请检查网络后重试。"
    return 1
  }
  echo "环境就绪：$VENV"
}

ensure_python() {
  if [ -x "$PY" ] && "$PY" -c "import yaml" >/dev/null 2>&1; then
    return 0
  fi
  # 记住上次选定的解释器（环境不在项目里，笔记也放用户目录）
  if [ -s "$PYNOTE" ]; then
    candidate="$(cat "$PYNOTE" 2>/dev/null)"
    if [ -n "$candidate" ] && [ -x "$candidate" ] && "$candidate" -c "import yaml" >/dev/null 2>&1; then
      PY="$candidate"
      return 0
    fi
  fi
  setup_python
}

run_py() { "$PY" run.py "$@"; }

case "$STEP" in
  setup)
    if [ -x "$PY" ] && "$PY" -c "import yaml" >/dev/null 2>&1; then
      echo "运行环境已经装好了：$VENV"
      echo "（想重装就先把这个文件夹删掉，再双击本文件）"
    else
      setup_python || { pause; exit 1; }
    fi
    ;;
  init-db)
    ensure_python || { pause; exit 1; }
    run_py init-db "$@"
    ;;
  rebuild)
    ensure_python || { pause; exit 1; }
    echo "重建前请先关掉正在看 data/market.db 的工具（DB Browser / Navicat）。"
    echo
    run_py init-db --rebuild || { echo; echo "重建取消，什么都没改。"; pause; exit 1; }
    echo
    echo "重新抓取数据 …"
    run_py daily --quiet
    run_py dictionary
    echo
    echo "完成。旧数据库保留在 data/market.db.bak-*"
    ;;
  selftest)
    ensure_python || { pause; exit 1; }
    run_py selftest
    ;;
  daily)
    ensure_python || { pause; exit 1; }
    run_py daily "$@"
    ;;
  report)
    ensure_python || { pause; exit 1; }
    run_py report "$@"
    ;;
  sources)
    ensure_python || { pause; exit 1; }
    run_py sources
    ;;
  install-sources)
    ensure_python || { pause; exit 1; }
    echo "=== 安装/升级免费数据源 ==="
    "$PY" -m pip install --quiet --upgrade adata akshare
    echo
    echo "=== 探测可用性 ==="
    run_py sources
    ;;
  dashboard)
    ensure_python || { pause; exit 1; }
    run_py dashboard "$@" && "$PY" run.py open dashboard
    ;;
  tables)
    ensure_python || { pause; exit 1; }
    echo "=== 表清单 ==="
    "$PY" sql.py tables
    echo
    echo "=== 生成浏览页面 db_view.html ==="
    "$PY" sql.py browser && "$PY" run.py open db_view
    ;;
  sql)
    if command -v sqlite3 >/dev/null 2>&1; then
      cat <<'TIP'
数据库：data/market.db
  .tables              列出所有表
  .schema alerts       看某张表的建表语句
  SELECT * FROM alerts LIMIT 10;
  .quit                退出

TIP
      sqlite3 data/market.db
    else
      ensure_python || { pause; exit 1; }
      echo "系统没有 sqlite3 命令行，改用 python sql.py 查询。"
      "$PY" sql.py tables
    fi
    ;;
  dictionary)
    ensure_python || { pause; exit 1; }
    run_py dictionary && "$PY" run.py open dictionary
    ;;
  auto)
    ensure_python || exit 1
    run_py daily --quiet
    run_py report
    exit 0
    ;;
  shortcut)
    target="$HOME/Desktop/每日任务.command"
    project="$(pwd)"
    printf '#!/bin/bash\ncd "%s" || exit 1\nexec bash "%s/macos/_mac-run.sh" daily\n' "$project" "$project" > "$target"
    chmod +x "$target"
    echo "已在桌面创建「每日任务.command」，双击即抓当日数据。"
    echo "（想固定到程序坞：右键该文件 → 选项 → 留在程序坞）"
    ;;
  web)
    ensure_python || { pause; exit 1; }
    echo "看盘页面马上启动，浏览器会自动打开。"
    echo "保持这个窗口开着；按 Control+C 或者直接关掉窗口就停止服务。"
    echo
    run_py web "$@"
    ;;
  share)
    ensure_python || { pause; exit 1; }
    echo "正在为自选里的每个标的生成看板图…"
    echo
    run_py share "$@"
    "$PY" run.py open share
    ;;
  review)
    ensure_python || { pause; exit 1; }
    run_py review "$@"
    ;;
  backtest)
    ensure_python || { pause; exit 1; }
    echo "正在把状态机的参数放到历史数据上重跑（几秒钟）…"
    echo
    run_py backtest "$@"
    "$PY" run.py open backtest
    ;;
  sync)
    ensure_python || { pause; exit 1; }
    echo "全市场本地化：先更新代码表，再分批补历史。"
    echo "可以反复双击，补过的会自动跳过；随时按 Control+C 中断都没问题。"
    echo
    run_py sync "$@"
    ;;
  shadow)
    ensure_python || { pause; exit 1; }
    run_py shadow "$@"
    ;;
  screen)
    ensure_python || { pause; exit 1; }
    echo "扫本地全部标的（有几只算几只，取决于 18-全市场同步 补了多少）…"
    echo
    run_py screen "$@"
    "$PY" run.py open screen
    ;;
  candidates)
    ensure_python || { pause; exit 1; }
    echo "从已有的筛选结果里排出观察池候选（只读本地数据，不会自己改观察池）…"
    echo
    run_py candidates "$@"
    ;;
  replay)
    ensure_python || { pause; exit 1; }
    echo "历史重放：把筛选条件在过去每一天跑一遍，跑完就能看到每个形态真实的 5 / 20 日表现。"
    echo "要几分钟——它会把全市场重算一遍，然后再顺着历史走。"
    echo
    run_py replay "$@"
    ;;
  pack)
    ensure_python || { pause; exit 1; }
    echo "打包数据库（先确认没有程序正在用它；压缩 480MB 大约要一分钟）…"
    echo
    run_py pack "$@"
    "$PY" run.py open pack
    ;;
  *)
    echo "未知步骤：$STEP"
    pause
    exit 1
    ;;
esac

pause
