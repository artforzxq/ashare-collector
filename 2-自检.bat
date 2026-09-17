@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Self test with offline fixture data ===
call "win-run.bat" selftest
echo.
pause
