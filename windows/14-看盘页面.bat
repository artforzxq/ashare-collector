@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo === Local chart page: K-line / indicators / watchlist ===
call "%~dp0win-run.bat" web
echo.
pause
