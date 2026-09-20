#!/bin/bash
# ============================================================
#  共享启动器（macOS 端）。Windows 端对应文件：win-run.bat
#  用法：bash _mac-run.sh <步骤> [附加参数]
#  步骤：setup init-db selftest daily report sources dashboard doctor
#        tables sql dictionary rebuild auto install-sources shortcut push
#
#  运行环境默认放在 ~/ashare-env，刻意不放进项目文件夹：
#  虚拟环境里有几百兆的 mac 专用文件，同步到 Windows 上只会变成垃圾。
#  想换位置：export ASHARE_VENV=/你的/路径
# ============================================================

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.." || exit 1

VENV="${ASHARE_VENV:-$HOME/ashare-env}"
PYNOTE="$HOME/ashare-python.txt"
DEPS="$HOME/ashare-deps.txt"
PY="$VENV/bin/python"
STEP="${1:-}"
shift 2>/dev/null || true

pause() {
  [ "${ASHARE_NO_PAUSE:-}" = "1" ] && return 0
  printf "\n按回车键关闭窗口… "
  read -r _ || true
}

# 数据源适配器装没装过。Windows 端用同一个思路（%USERPROFILE%\ashare-deps.txt）：
# 装成功才写标记，没标记就自动装一次——否则每次日终都在报"akshare 没装"。
adapters_ready() {
  [ -f "$DEPS" ] || return 1
  head -n 1 "$DEPS" 2>/dev/null | grep -q '^ok' 2>/dev/null || return 1
  return 0
}

# 数据库：不校验"装没装"（它是文件，用到就建），但**缺了就顺手建**：
# 全是 CREATE TABLE IF NOT EXISTS，跑一次 init-db 就有一张空库，之后跑日终才有数据。
ensure_db() {
  [ -f data/market.db ] && return 0
  echo "还没有数据库，先建一张空库（之后跑 3-每日任务 才会有数据）…"
  "$PY" run.py init-db >/dev/null
}

install_adapters() {
  echo "=== 安装数据源适配器（只做一次）==="
  "$PY" -m pip --version >/dev/null 2>&1 || "$PY" -m ensurepip --default-pip >/dev/null 2>&1
  if ! "$PY" -m pip install --quiet pyyaml; then
    echo "  [!] PyYAML 装不上——它只在这台机器有依赖，核心链路就靠它。"
    return 1
  fi
  if "$PY" -m pip install --quiet baostock akshare; then
    echo ok > "$DEPS"
    echo "  数据源已就绪（baostock + akshare）"
    return 0
  fi
  # akshare 依赖的 jsonpath 只发布源码包，而那份包的 setup.py 会 import 自己，
  # pip 的隔离构建里必然失败。仓库 tools/vendor 里放了一个只改打包脚本的 wheel。
  echo "  [!] akshare 直装失败，改用仓库自带的 jsonpath wheel 重试 …"
  if "$PY" -m pip install --quiet "tools/vendor/jsonpath-0.82.2-py3-none-any.whl" baostock akshare; then
    echo ok > "$DEPS"
    echo "  数据源已就绪（baostock + akshare）"
    return 0
  fi
  echo "  [!] 可选数据源没装上，网络空的时候再跑一次「0-安装环境」。"
  echo "      （不装也能跑，只是数据源自检会少两个；标记留成 partial，一周内不再重试）"
  echo partial > "$DEPS"
  return 0
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
  "$VENV/bin/python" -m pip install --quiet pyyaml || {
    echo "安装依赖失败，请检查网络后重试。"
    return 1
  }
  PY="$VENV/bin/python"
  install_adapters || true
  echo "环境就绪：$VENV"
}

ensure_python() {
  if [ -x "$PY" ] && "$PY" -c "import yaml" >/dev/null 2>&1; then
    if ! adapters_ready; then
      case "$(head -n 1 "$DEPS" 2>/dev/null)" in
        partial)
          # 上次只装上一部分：一周内不再打扰（免得每个交易日都白等一次 pip）
          if [ -n "$(find "$DEPS" -mtime -7 2>/dev/null)" ]; then return 0; fi
          ;;
      esac
      install_adapters || true
    fi
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

run_py() { ensure_db; "$PY" run.py "$@"; }

usage() {
  cat <<'TIP'
这是共享启动器，给带编号的 .command 文件调用，不用自己打开。
要跑什么，双击 macos/ 里对应的那个：

   0-安装环境      准备 Python 环境（每台电脑第一次）
   2-自检          离线夹具跑通全链路
   3-每日任务      抓行情 → 算状态 → 出提醒
   7-看数据库      生成网页版表浏览器
  11-生成字段说明  导出 字段说明.md
  14-看盘页面      本地看盘界面（日常主力）
  16-信号复盘      回填提醒的真实表现
  17-参数回测      扫状态机参数，看哪组真有效
  18-全市场同步    分批补全市场历史（可反复点）
  19-影子因子体检  影子因子够不够格转正
  20-全市场筛选    按形态扫本地全部标的
  21-观察池候选    谁该进池子、谁该出来
  22-历史重放      把筛选条件在过去每一天重跑
  23-推送简报      把当天简报推到手机
  24-自动运行      每个工作日收盘后自动跑（开关）
  25-环境体检      Python / 依赖 / 数据库 / 配置 一次看全
  10-创建桌面快捷方式

命令行用法：bash macos/_mac-run.sh <步骤> [参数]
  例如：bash macos/_mac-run.sh daily
        bash macos/_mac-run.sh doctor
        bash macos/_mac-run.sh push --test

可用步骤：setup init-db selftest daily report sources dashboard doctor
          tables sql dictionary rebuild auto install-sources shortcut push
          web share review backtest sync shadow screen candidates replay pack
TIP
}

case "$STEP" in
  "")
    usage
    pause
    exit 0
    ;;
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
  doctor)
    ensure_python || { pause; exit 1; }
    echo "体检内容：Python / 依赖 / 数据源可用性 / 数据库 / 配置。只读，不会改任何东西。"
    echo "会真的取一次数（沪深300 当探针）来看哪个数据源现在能用，大约 10~30 秒。"
    echo
    run_py doctor --sources
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
    # 后台任务不看日志，所以这里要把它记下来：库缺了建库、环境有问题先说清楚
    ensure_db
    run_py doctor >/dev/null 2>&1 || echo "  [!] 环境体检有问题，跑一次 25-环境体检 看看"
    run_py daily --quiet
    run_py report
    run_py push
    exit 0
    ;;
  push)
    ensure_python || { pause; exit 1; }
    run_py push "$@"
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
  buckets)
    ensure_python || { pause; exit 1; }
    echo "分档研究：把全市场按某个维度分档（位置 / 量能 / 成交额 / 距支撑…），看之后 20 天怎么样。"
    echo "只读本地日线，半分钟左右。加 --field 或 --cross 可以只算关心的那几维。"
    echo
    run_py buckets "$@"
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
