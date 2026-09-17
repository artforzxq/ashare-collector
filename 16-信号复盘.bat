@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Review: how did past alerts actually perform ===
call "win-run.bat" review
echo.
pause
