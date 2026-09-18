@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo === Watchlist candidates ===
echo Reads the stored screen results; nothing is added to the watchlist by itself.
echo Copy the printed "run.py add ..." line to put a symbol into the watchlist.
call "%~dp0win-run.bat" candidates
echo.
pause
