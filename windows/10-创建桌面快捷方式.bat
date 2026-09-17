@echo off
chcp 65001 >nul
cd /d "%~dp0.."
call "%~dp0win-run.bat" shortcut
echo.
pause
