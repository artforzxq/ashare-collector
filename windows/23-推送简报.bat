@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo === Push today's briefing to your phone (PushPlus) ===
rem Add --test on the command line to send a test message instead.
call "%~dp0win-run.bat" push
echo.
pause
