@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo === Environment check: python / packages / database / config ===
echo Read-only. It never changes anything.
call "%~dp0win-run.bat" doctor
echo.
pause
