@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo === Build data dictionary and docs ===
call "%~dp0win-run.bat" dictionary
echo.
pause
