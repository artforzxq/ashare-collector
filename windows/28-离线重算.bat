@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo === Recompute features / levels / risk / alerts from local bars ===
echo No network: it does not fetch bars, snapshots, calendars or shares.
echo Use it when the daily bars are already in the database but the conclusions
echo   (features / key levels / alerts) are still from an older date.
echo Before the close it is the safe way to refresh - it will not take a half-day bar.
call "%~dp0win-run.bat" daily --offline
echo.
pause
