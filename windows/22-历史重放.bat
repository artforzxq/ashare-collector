@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo === Replay the screen criteria over history ===
echo Rewrites the screen results for the last 240 trading days, then reports
echo the real 5 / 20-day performance of every pattern. Takes several minutes.
call "%~dp0win-run.bat" replay --days 240
echo.
pause
