#!/bin/bash
# 双击运行：让「每日任务」在每个工作日收盘后自己跑（macOS launchd）。
#
# 默认 15:35 跑一次：抓当日行情 → 算状态 → 出简报 → 推到手机。
# 想换时间就带参数（终端里）：bash macos/24-自动运行.command 15:50
# 想关掉：                      bash macos/24-自动运行.command off
#
# 为什么用 launchd 而不是页面上的任务卡：关机重启、没打开浏览器的时候它也会跑，
# 而且按日历触发，不依赖任何窗口开着。
set -u

LABEL="com.ashare-collector.daily"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROJECT="$(cd "$HERE/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOGS="$PROJECT/logs"
WHEN="${1:-15:35}"

pause() {
  [ "${ASHARE_NO_PAUSE:-}" = "1" ] && return 0
  printf "\n按回车键关闭窗口… "
  read -r _ || true
}

unload() {
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || \
    launchctl unload "$PLIST" 2>/dev/null || true
}

if [ "$WHEN" = "off" ]; then
  unload
  rm -f "$PLIST"
  echo "已关闭自动运行（并删掉 $PLIST）。"
  echo "每日任务还在，想手动跑就双击 3-每日任务。"
  pause
  exit 0
fi

case "$WHEN" in
  [0-2][0-9]:[0-5][0-9]) ;;
  *) echo "时间格式应为 HH:MM（例如 15:35），或者传 off 关闭。"; pause; exit 1 ;;
esac
HOUR="${WHEN%%:*}"
MIN="${WHEN##*:}"

mkdir -p "$HOME/Library/LaunchAgents" "$LOGS"
unload

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$PROJECT/macos/_mac-run.sh</string>
    <string>auto</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$PROJECT</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>ASHARE_NO_PAUSE</key>
    <string>1</string>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <!-- 周一到周五，${WHEN}。节假日照跑一次也没关系：日终任务按交易日算，
       同一天的简报只推一次，不会重复吵你。 -->
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
  </array>
  <!-- 开机/登录时不补跑：补跑会在一大早抓昨天的数据，简报看着莫名其妙。 -->
  <key>RunAtLoad</key>
  <false/>
  <key>StandardOutPath</key>
  <string>$LOGS/launchd-daily.log</string>
  <key>StandardErrorPath</key>
  <string>$LOGS/launchd-daily.log</string>
</dict>
</plist>
PLIST_EOF

if launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl load -w "$PLIST" 2>/dev/null; then
  echo "已开启自动运行：每个工作日 $WHEN"
  echo "  执行内容：抓当日行情 → 算状态 → 出简报 → 推到手机"
  echo "  日志：    $LOGS/launchd-daily.log（以及 logs/daily.log）"
  echo "  手动试跑：launchctl kickstart -k gui/$(id -u)/$LABEL"
  echo "  关闭：    bash macos/24-自动运行.command off"
else
  echo "注册失败。可以手动试一次："
  echo "  launchctl bootstrap gui/$(id -u) \"$PLIST\""
  pause
  exit 1
fi

pause
