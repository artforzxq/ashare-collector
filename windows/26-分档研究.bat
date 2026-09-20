@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo === Bucket the whole market by one dimension ===
echo Position / volume / turnover / distance-to-support, and the 20-day result of each bucket.
echo Reads local bars only; about half a minute.
call "%~dp0win-run.bat" buckets
echo.
pause
