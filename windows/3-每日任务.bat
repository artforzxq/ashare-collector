@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo === Daily job: fetch bars, compute state, emit alerts ===
call "%~dp0win-run.bat" daily
echo.
pause
