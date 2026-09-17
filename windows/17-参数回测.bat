@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo === Backtest: which thresholds actually work ===
call "%~dp0win-run.bat" backtest
echo.
pause
