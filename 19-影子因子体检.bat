@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Factor ledger health check (shadow factors) ===
call "win-run.bat" shadow
echo.
pause
