@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo === Screen every locally stored symbol by pattern ===
echo Needs data from step 18 first; the report is saved next to the database.
call "%~dp0win-run.bat" screen
echo.
pause
