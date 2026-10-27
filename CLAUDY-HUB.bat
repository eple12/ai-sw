@echo off
REM CLAUDY HUB one-click launcher: chess engine + Gomoku + shape recognition in one window.
REM First run: finds (or installs) Python 3.10-3.13, creates claudy-hub\.venv, installs the chess dashboard's
REM libraries, writes its settings. Every later run just starts the hub.
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0claudy-hub"
title CLAUDY HUB

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import textual, chess, httpx, yaml" >nul 2>nul
    if not errorlevel 1 goto start
)

echo.
echo  ===== CLAUDY HUB setup (first run only) =====
echo.
set "PYEXE="
call :find_python
if defined PYEXE goto have_python

echo [1/3] Python was not found. Installing Python 3.12 for this user...
where winget >nul 2>nul
if not errorlevel 1 (
    winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
)
call :find_python
if defined PYEXE goto have_python
set "PYINST=%TEMP%\python-3.12.8-amd64.exe"
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe' -OutFile '%PYINST%'"
if exist "%PYINST%" "%PYINST%" /quiet InstallAllUsers=0 PrependPath=0 Include_launcher=0 Include_test=0 Include_doc=0
call :find_python
if defined PYEXE goto have_python
echo [ERROR] Could not find or install Python. Install Python 3.12 from https://www.python.org/downloads/ and run this file again.
goto fail

:have_python
echo [1/3] Python: %PYEXE%
if not exist ".venv\Scripts\python.exe" (
    echo [2/3] Creating the virtual environment...
    %PYEXE% -m venv .venv
    if errorlevel 1 goto fail
)
echo [3/3] Installing libraries...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r apps\chess\bot\requirements-android.txt
if errorlevel 1 goto fail

if not exist "apps\chess\bot\config.yml" (
    set /a THREADS=%NUMBER_OF_PROCESSORS%/2
    if !THREADS! lss 1 set THREADS=1
    if !THREADS! gtr 8 set THREADS=8
    (
        echo engine:
        echo   path: ../claudy-avx2.exe
        echo   ponder: false
        echo   options:
        echo     Hash: 256
        echo     Threads: !THREADS!
        echo web:
        echo   enabled: true
        echo local:
        echo   elo: 1500
    ) > "apps\chess\bot\config.yml"
)

:start
".venv\Scripts\python.exe" hub.py
exit /b 0

:fail
echo.
echo [ERROR] Setup did not finish - see the messages above. (Needs an internet connection; run this file again to retry.)
pause
exit /b 1

REM ---- sets PYEXE to a usable interpreter (3.12, 3.13, 3.11, 3.10), or leaves it unset
:find_python
set "PYEXE="
for %%V in (3.12 3.13 3.11 3.10) do (
    if not defined PYEXE (
        py -%%V -c "import sys" >nul 2>nul
        if not errorlevel 1 set "PYEXE=py -%%V"
    )
)
if not defined PYEXE (
    python -c "import sys; sys.exit(0 if (3,10)<=sys.version_info[:2]<=(3,13) else 1)" >nul 2>nul
    if not errorlevel 1 set "PYEXE=python"
)
exit /b 0
