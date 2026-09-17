@echo off
rem ============================================================
rem  Shared launcher (Windows). macOS counterpart: _mac-run.sh
rem  Usage: call win-run.bat <step> [extra args]
rem  Steps: setup init-db selftest daily report sources dashboard
rem         tables sql dictionary rebuild auto install-sources shortcut
rem
rem  Python env lives in %USERPROFILE%\ashare-env on purpose:
rem  a virtualenv is platform specific, keeping it out of the project
rem  folder keeps the folder safe to copy to the Mac.
rem  Override with: set ASHARE_VENV=D:\some\path
rem ============================================================
setlocal
cd /d "%~dp0"
chcp 65001 >nul

if defined ASHARE_VENV (set "VENV=%ASHARE_VENV%") else (set "VENV=%USERPROFILE%\ashare-env")
set "PY=%VENV%\Scripts\python.exe"
set "STEP=%~1"
set "EXTRA=%2 %3 %4 %5 %6 %7 %8 %9"

if /i "%STEP%"=="setup" goto :setup
if not exist "%PY%" goto :setup
"%PY%" -c "import yaml, baostock" >nul 2>&1
if errorlevel 1 goto :setup
goto :dispatch

:setup
echo === Preparing the python environment (first run takes 1-2 minutes) ===
if not exist "%PY%" goto :do_setup
"%PY%" -c "import yaml, baostock" >nul 2>&1
if not errorlevel 1 (
  echo Environment already ready: %VENV%
  echo To reinstall, delete that folder and run this again.
  exit /b 0
)
:do_setup
set "BASE="
where py >nul 2>&1
if not errorlevel 1 set "BASE=py -3"
if defined BASE goto :have_base
where python >nul 2>&1
if not errorlevel 1 set "BASE=python"
:have_base
if not defined BASE goto :no_python
%BASE% -m venv "%VENV%"
if errorlevel 1 exit /b 1
"%PY%" -m pip install --quiet --upgrade pip
"%PY%" -m pip install --quiet pyyaml baostock akshare
if errorlevel 1 goto :pip_failed
echo Environment ready: %VENV%
if /i "%STEP%"=="setup" exit /b 0
goto :dispatch

:no_python
echo Python not found. Install Python 3.10+ from https://www.python.org/downloads/
echo Tick "Add python.exe to PATH" during setup, then run this again.
exit /b 1

:pip_failed
echo Failed to install packages. Check the network and try again.
exit /b 1

:dispatch
if /i "%STEP%"=="init-db"         goto :init_db
if /i "%STEP%"=="rebuild"         goto :rebuild
if /i "%STEP%"=="selftest"        goto :selftest
if /i "%STEP%"=="daily"           goto :daily
if /i "%STEP%"=="report"          goto :report
if /i "%STEP%"=="sources"         goto :sources
if /i "%STEP%"=="install-sources" goto :install_sources
if /i "%STEP%"=="dashboard"       goto :dashboard
if /i "%STEP%"=="tables"          goto :tables
if /i "%STEP%"=="sql"             goto :sql
if /i "%STEP%"=="dictionary"      goto :dictionary
if /i "%STEP%"=="shortcut"        goto :shortcut
if /i "%STEP%"=="web"             goto :web
if /i "%STEP%"=="share"           goto :share
if /i "%STEP%"=="review"          goto :review
if /i "%STEP%"=="backtest"        goto :backtest
if /i "%STEP%"=="sync"            goto :sync
if /i "%STEP%"=="shadow"          goto :shadow
if /i "%STEP%"=="screen"          goto :screen
if /i "%STEP%"=="pack"            goto :pack
echo Unknown step: %STEP%
exit /b 1

:init_db
"%PY%" run.py init-db %EXTRA%
exit /b %ERRORLEVEL%

:rebuild
echo Close DB Browser / Navicat connections to data\market.db first.
echo.
"%PY%" run.py init-db --rebuild
if errorlevel 1 goto :rebuild_failed
echo.
echo Repopulating data ...
"%PY%" run.py daily --quiet
"%PY%" run.py dictionary
echo.
echo Done. Old database was kept as data\market.db.bak-*
exit /b 0

:rebuild_failed
echo.
echo Rebuild cancelled. Nothing was changed.
exit /b 1

:selftest
"%PY%" run.py selftest
exit /b %ERRORLEVEL%

:daily
"%PY%" run.py daily %EXTRA%
exit /b %ERRORLEVEL%

:report
"%PY%" run.py report %EXTRA%
exit /b %ERRORLEVEL%

:sources
"%PY%" run.py sources
exit /b %ERRORLEVEL%

:install_sources
echo === Install / upgrade free data sources ===
"%PY%" -m pip install --quiet --upgrade adata akshare
echo.
echo === Probe availability ===
"%PY%" run.py sources
exit /b %ERRORLEVEL%

:dashboard
"%PY%" run.py dashboard %EXTRA%
if errorlevel 1 exit /b 1
if exist dashboard.html start "" dashboard.html
exit /b 0

:tables
echo === Tables ===
"%PY%" sql.py tables
echo.
echo === Build db_view.html ===
"%PY%" sql.py browser
if exist db_view.html start "" db_view.html
exit /b 0

:sql
echo SQLite CLI opened. Database: data\market.db
echo   .tables              list tables
echo   .schema alerts       show table DDL
echo   .quit                exit
echo.
"tools\sqlite3.exe" "data\market.db"
exit /b 0

:dictionary
"%PY%" run.py dictionary
if errorlevel 1 exit /b 1
if exist "字段说明.md" start "" "字段说明.md"
exit /b 0

:shortcut
powershell -NoProfile -ExecutionPolicy Bypass -File "tools\make_shortcut.ps1" -Target Desktop
if errorlevel 1 goto :shortcut_failed
echo.
echo Shortcut created on Desktop. Right-click it and choose "Pin to Start" if you like.
exit /b 0

:shortcut_failed
echo.
echo Failed. Check tools\DBBrowser exists.
exit /b 1

:web
echo Starting the local chart page. The browser will open automatically.
echo Keep this window open; press Ctrl+C to stop the server.
echo.
"%PY%" run.py web %EXTRA%
exit /b %ERRORLEVEL%

:share
echo Building share cards for the watchlist ...
echo.
"%PY%" run.py share %EXTRA%
if errorlevel 1 exit /b 1
if exist "分享图" start "" "分享图"
exit /b 0

:review
"%PY%" run.py review %EXTRA%
exit /b %ERRORLEVEL%

:backtest
echo Re-running the state machine over history ...
echo.
"%PY%" run.py backtest %EXTRA%
if errorlevel 1 exit /b 1
if exist "回测" start "" "回测"
exit /b 0

:sync
echo Building the local full-market warehouse.
echo Re-run as many times as you like; finished symbols are skipped.
echo.
"%PY%" run.py sync %EXTRA%
exit /b %ERRORLEVEL%

:shadow
"%PY%" run.py shadow %EXTRA%
exit /b %ERRORLEVEL%

:screen
echo Screening every locally stored symbol ...
echo.
"%PY%" run.py screen %EXTRA%
if errorlevel 1 exit /b 1
if exist "筛选" start "" "筛选"
exit /b 0

:pack
echo Packing the database so you can carry it to another computer ...
echo.
"%PY%" run.py pack %EXTRA%
if errorlevel 1 exit /b 1
if exist "备份" start "" "备份"
exit /b 0
