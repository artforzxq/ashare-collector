@echo off
rem ============================================================
rem  Shared launcher (Windows). macOS counterpart: macos/_mac-run.sh
rem  This file lives in windows\; the project root is its parent folder.
rem  Usage: call "%~dp0win-run.bat" <step> [extra args]
rem  Steps: setup init-db selftest daily report sources dashboard
rem         tables sql dictionary rebuild auto install-sources shortcut db-shortcut
rem         web share review backtest sync shadow screen candidates pack push doctor
rem
rem  Keep this file pure ASCII. cmd.exe reads a .bat byte by byte using the
rem  console code page, so a UTF-8 file containing Chinese gets mis-parsed and
rem  every line after it turns into "is not recognized as a command". Anything
rem  that needs a Chinese path goes through:  "%PY%" run.py open <key>
rem
rem  Which python is used, in order:
rem    1. %ASHARE_PYTHON% - set this to force a specific interpreter
rem    2. an existing virtualenv at %USERPROFILE%\ashare-env
rem    3. the interpreter remembered from the last setup
rem       (%USERPROFILE%\ashare-python.txt)
rem    4. whatever python this computer has (py launcher / python3 / python /
rem       the usual install folders) - used directly, no virtualenv needed
rem
rem  Only PyYAML is required; the data source adapters are optional and can be
rem  installed later from the install-sources step. A virtualenv is created
rem  only when you ask for one with: set ASHARE_VENV=D:\some\path
rem ============================================================
setlocal
cd /d "%~dp0.."
chcp 65001 >nul

if defined ASHARE_VENV (set "VENV=%ASHARE_VENV%") else (set "VENV=%USERPROFILE%\ashare-env")
set "PYNOTE=%USERPROFILE%\ashare-python.txt"
set "DEPS=%USERPROFILE%\ashare-deps.txt"
set "STEP=%~1"
set "EXTRA=%2 %3 %4 %5 %6 %7 %8 %9"
set "PY="

rem Double-clicked with no step? Say so instead of flashing a window and vanishing.
if "%~1"=="" goto :no_step

rem ---- 1) is there an interpreter we can use right now? ----
if defined ASHARE_PYTHON (
  call :probe "%ASHARE_PYTHON%"
  if defined BASE set "PY=%BASE%"
)
if not defined PY if exist "%VENV%\Scripts\python.exe" set "PY=%VENV%\Scripts\python.exe"
if not defined PY if exist "%PYNOTE%" set /p PY=<"%PYNOTE%"
if defined PY if not exist "%PY%" set "PY="

if defined PY (
  "%PY%" -c "import yaml" >nul 2>&1
  if not errorlevel 1 if exist "%DEPS%" goto :dispatch
)
goto :setup

:setup
echo === Preparing the python environment ===
set "BASE="
set "PREV="
if exist "%PYNOTE%" set /p PREV=<"%PYNOTE%"
if defined PREV call :probe "%PREV%"
if not defined BASE call :probe "py -3"
if not defined BASE call :probe "python3"
if not defined BASE call :probe "python"
if not defined BASE for %%P in ("%LOCALAPPDATA%\Programs\Python\Python3*\python.exe" "C:\Python3*\python.exe" "D:\Python3*\python.exe") do if not defined BASE call :probe "%%~fP"
if not defined BASE goto :no_python

echo Using python: %BASE%
set "PY=%BASE%"

rem A virtualenv is opt-in: it is platform specific, so it stays outside the
rem project folder and is only built when explicitly asked for.
if defined ASHARE_VENV if not exist "%VENV%\Scripts\python.exe" (
  "%BASE%" -c "import venv, ensurepip" >nul 2>&1
  if not errorlevel 1 (
    echo Creating a virtual environment: %VENV%
    "%BASE%" -m venv "%VENV%"
    if not errorlevel 1 set "PY=%VENV%\Scripts\python.exe"
  )
)

rem No marker yet means the optional adapters were never installed - do it once,
rem otherwise every daily run keeps reporting "akshare not installed".
if not exist "%DEPS%" goto :install_deps
"%PY%" -c "import yaml" >nul 2>&1
if not errorlevel 1 goto :remember

:install_deps
echo Installing the packages it needs ...
call :install
if errorlevel 1 exit /b 1

:remember
2>nul >"%PYNOTE%" echo %PY%
echo Environment ready: %PY%
if /i "%STEP%"=="setup" exit /b 0
goto :dispatch

rem ---- helpers -------------------------------------------------

:probe
rem %~1 = candidate: a full path to python.exe, or a command like "py -3".
rem On success BASE holds a real path, so "%BASE%" is safe to quote everywhere.
set "RESOLVED="
for /f "delims=" %%I in ('"%~1" -c "import sys;print(sys.executable)" 2^>nul') do set "RESOLVED=%%I"
if not defined RESOLVED for /f "delims=" %%I in ('%~1 -c "import sys;print(sys.executable)" 2^>nul') do set "RESOLVED=%%I"
if defined RESOLVED if exist "%RESOLVED%" set "BASE=%RESOLVED%"
exit /b 0

:install
rem PyYAML is the only hard requirement; the adapters are optional.
"%PY%" -m pip --version >nul 2>&1
if errorlevel 1 (
  echo pip is missing on this python, trying ensurepip ...
  "%PY%" -m ensurepip --default-pip >nul 2>&1
)
"%PY%" -m pip --version >nul 2>&1
if errorlevel 1 goto :no_pip
"%PY%" -m pip install --quiet pyyaml
if errorlevel 1 goto :pip_failed
"%PY%" -m pip install --quiet baostock akshare
if not errorlevel 1 goto :install_ok
rem akshare needs jsonpath, and jsonpath only ships as a source package whose
rem setup.py imports itself - that breaks pip's isolated build. A wheel with the
rem same untouched jsonpath.py (only the packaging script fixed) sits in
rem tools\vendor; installing it first satisfies the dependency and pip never
rem has to build the broken sdist.
echo   [!] akshare install failed, retrying with the bundled jsonpath wheel ...
"%PY%" -m pip install --quiet "tools\vendor\jsonpath-0.82.2-py3-none-any.whl" baostock akshare
if errorlevel 1 goto :optional_failed

:install_ok
2>nul >"%DEPS%" echo ok
exit /b 0

:optional_failed
echo   [!] Optional data sources not installed - retry when the network is free.
exit /b 0

:no_python
echo.
echo Python not found on this computer.
echo Install Python 3.10 or newer from https://www.python.org/downloads/
echo Tick "Add python.exe to PATH" during setup, then run this again.
exit /b 1

:no_pip
echo.
echo This python has no pip, so packages cannot be installed into it.
echo Install the official Python from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH", then run this again.
exit /b 1

:pip_failed
echo.
echo Failed to install PyYAML. Check the network and try again.
exit /b 1

:no_step
echo This is the shared launcher. It is called by the numbered files, not by hand.
echo Double-click one of those instead. They are named like "3-<job>.bat":
echo.
echo    0   prepare the python environment (first time on a machine)
echo    2   offline self test
echo    3   daily job: fetch bars, compute state, emit alerts
echo    7   browse the database
echo   11   build the field dictionary
echo   14   local chart page (daily K line + intraday)
echo   16   fill in how past alerts actually performed
echo   17   parameter backtest
echo   18   build the local full-market warehouse (run it a few times)
echo   19   factor ledger health check
echo   20   screen every stored symbol by pattern
echo   21   watchlist candidates: who should be in, who should be out
echo   22   replay the screen criteria over history (real 5/20-day results)
echo   23   push today's briefing to the phone
echo   24   turn the daily job into a scheduled task (on / off)
echo   25   environment check: python / packages / database / config
echo   10   create a desktop shortcut for the daily job
echo.
echo From a command line: win-run.bat STEP [args]   e.g.  win-run.bat daily
echo.
pause
exit /b 0

rem ---- dispatch ------------------------------------------------

:dispatch
rem A missing database is not an error: init-db creates an empty one (every
rem statement is CREATE TABLE IF NOT EXISTS), the daily job fills it later.
if not exist "data\market.db" (
  echo No database yet - creating an empty one. Run 3-daily to fill it.
  "%PY%" run.py init-db >nul 2>&1
)
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
if /i "%STEP%"=="db-shortcut"     goto :db_shortcut
if /i "%STEP%"=="web"             goto :web
if /i "%STEP%"=="share"           goto :share
if /i "%STEP%"=="review"          goto :review
if /i "%STEP%"=="backtest"        goto :backtest
if /i "%STEP%"=="sync"            goto :sync
if /i "%STEP%"=="shadow"          goto :shadow
if /i "%STEP%"=="screen"          goto :screen
if /i "%STEP%"=="candidates"      goto :candidates
if /i "%STEP%"=="replay"          goto :replay
if /i "%STEP%"=="pack"            goto :pack
if /i "%STEP%"=="push"            goto :push
if /i "%STEP%"=="doctor"          goto :doctor
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
echo Done. The old database was kept as data\market.db.bak-*
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
"%PY%" -m pip install --quiet --upgrade "tools\vendor\jsonpath-0.82.2-py3-none-any.whl" adata akshare
echo.
echo === Probe availability ===
"%PY%" run.py sources
exit /b %ERRORLEVEL%

:dashboard
"%PY%" run.py dashboard %EXTRA%
if errorlevel 1 exit /b 1
"%PY%" run.py open dashboard
exit /b 0

:tables
echo === Tables ===
"%PY%" sql.py tables
echo.
echo === Build db_view.html ===
"%PY%" sql.py browser
"%PY%" run.py open db_view
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
"%PY%" run.py open dictionary
exit /b 0

:shortcut
powershell -NoProfile -ExecutionPolicy Bypass -File "tools\make_daily_shortcut.ps1" -Target Desktop
if errorlevel 1 goto :shortcut_failed
echo.
echo Shortcut created on Desktop: double-click it to run the daily job.
echo Right-click it and choose "Pin to Start" if you like.
exit /b 0

:shortcut_failed
echo.
echo Failed to create the shortcut.
exit /b 1

:db_shortcut
echo Creating a Desktop shortcut for DB Browser ...
powershell -NoProfile -ExecutionPolicy Bypass -File "tools\make_shortcut.ps1" -Target Desktop
if errorlevel 1 goto :shortcut_failed
echo.
echo Shortcut created on Desktop. Right-click it and choose "Pin to Start" if you like.
exit /b 0

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
"%PY%" run.py open share
exit /b 0

:review
"%PY%" run.py review %EXTRA%
exit /b %ERRORLEVEL%

:backtest
echo Re-running the state machine over history ...
echo.
"%PY%" run.py backtest %EXTRA%
if errorlevel 1 exit /b 1
"%PY%" run.py open backtest
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
"%PY%" run.py open screen
exit /b 0

:candidates
echo Ranking watchlist candidates from the stored screen results ...
echo This only reads local data; nothing is added to the watchlist by itself.
echo.
"%PY%" run.py candidates %EXTRA%
exit /b %ERRORLEVEL%

:replay
echo Replaying the screen criteria over past trading days ...
echo Takes several minutes: it recomputes every symbol once, then walks back through history.
echo After it finishes you get the real 5 / 20-day performance of each pattern.
echo.
"%PY%" run.py replay %EXTRA%
exit /b %ERRORLEVEL%

:pack
echo Packing the database so you can carry it to another computer ...
echo.
"%PY%" run.py pack %EXTRA%
if errorlevel 1 exit /b 1
"%PY%" run.py open pack
exit /b 0

:push
rem Sends today's briefing to the phone through PushPlus.
rem The token lives in data\pushplus.token (gitignored) or ASHARE_PUSHPLUS_TOKEN.
echo Pushing today's briefing to your phone ...
echo.
"%PY%" run.py push %EXTRA%
exit /b %ERRORLEVEL%

:doctor
rem Environment check: python / packages / data sources / database / config.
rem Read-only. The --sources flag really fetches once per adapter (takes ~10-30s).
echo Checking the environment (python, packages, data sources, database, config) ...
echo This really fetches data once per adapter to see which ones work right now.
echo.
"%PY%" run.py doctor --sources %EXTRA%
exit /b %ERRORLEVEL%
