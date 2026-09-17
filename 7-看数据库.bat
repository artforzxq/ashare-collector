@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "win-run.bat" tables
echo.
pause
