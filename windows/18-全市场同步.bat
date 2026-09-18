@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo === Sync the full-market warehouse in batches ===
echo Re-run as many times as you like; symbols already up to date are skipped.
echo.
call "%~dp0win-run.bat" sync
echo.
pause
