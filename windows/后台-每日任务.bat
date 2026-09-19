@echo off
rem Runs the daily job and appends everything to logs\daily.log.
rem
rem Double-clicked: it shows the tail of the log and waits for a key, so the
rem window does not vanish before you can read anything.
rem Called by Task Scheduler: set ASHARE_NO_PAUSE=1 in that task, otherwise the
rem task will sit there waiting for a keypress it will never get.
rem Pure ASCII on purpose - cmd mis-parses a UTF-8 .bat that contains Chinese.
chcp 65001 >nul
cd /d "%~dp0.."
if not exist logs mkdir logs
echo. >> "logs\daily.log"
echo ===== %date% %time% ===== >> "logs\daily.log"
call "%~dp0win-run.bat" daily --quiet >> "logs\daily.log" 2>&1
call "%~dp0win-run.bat" report >> "logs\daily.log" 2>&1
rem Push the briefing to the phone. A missing token only logs a line, it never fails the job.
call "%~dp0win-run.bat" push >> "logs\daily.log" 2>&1
echo [done] >> "logs\daily.log"

if defined ASHARE_NO_PAUSE exit /b 0
echo.
echo ==== tail of logs\daily.log ====
powershell -NoProfile -Command "Get-Content -Encoding UTF8 -Tail 15 'logs\daily.log'"
echo.
pause
