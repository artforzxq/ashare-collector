@echo off
rem Called by Windows Task Scheduler: no window, no pause, log to logs\daily.log
chcp 65001 >nul
cd /d "%~dp0"
if not exist logs mkdir logs
echo. >> "logs\daily.log"
echo ===== %date% %time% ===== >> "logs\daily.log"
call "win-run.bat" daily --quiet >> "logs\daily.log" 2>&1
call "win-run.bat" report >> "logs\daily.log" 2>&1
echo [done] >> "logs\daily.log"
