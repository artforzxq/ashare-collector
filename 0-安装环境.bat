@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Install python environment (first time only) ===
call "win-run.bat" setup
echo.
pause
