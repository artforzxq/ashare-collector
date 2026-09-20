@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo === One-symbol check-up ===
echo Prints state / factors / position / patterns / levels / risk / alerts / screen history,
echo plus where this symbol sits in the market-wide buckets.
echo.
set /p CODE=Stock code (e.g. 600519 or SH600519):
if "%CODE%"=="" (
  echo No code given.
  pause
  exit /b 1
)
echo.
call "%~dp0win-run.bat" dig %CODE%
echo.
pause
