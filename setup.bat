@echo off
REM FORMULA-AI one-click setup. Double-click it (or run it from a terminal).
REM   1. finds Python 3.10-3.12 -- installs 3.12 for this user if there is none
REM   2. creates a private virtual environment in .venv\
REM   3. installs the packages in requirements.txt
REM Run it again any time to repair or update the environment.
setlocal EnableExtensions
cd /d "%~dp0"
title FORMULA-AI setup
echo.
echo  ===== FORMULA-AI setup =====
echo.

set "PYEXE="
call :find_python
if defined PYEXE goto have_python

echo [1/3] Python 3.10-3.12 was not found. Installing Python 3.12 for this user...
where winget >nul 2>nul
if not errorlevel 1 (
    winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
)
call :find_python
if defined PYEXE goto have_python

echo       winget is not available or did not finish - downloading the installer from python.org...
set "PYINST=%TEMP%\python-3.12.8-amd64.exe"
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe' -OutFile '%PYINST%'"
if not exist "%PYINST%" goto no_python
"%PYINST%" /quiet InstallAllUsers=0 PrependPath=0 Include_launcher=0 Include_test=0 Include_doc=0
call :find_python
if defined PYEXE goto have_python

:no_python
echo.
echo [ERROR] Could not find or install Python.
echo         Install Python 3.12 from https://www.python.org/downloads/ ,
echo         then run setup.bat again.
goto fail

:have_python
echo [1/3] Python: %PYEXE%
if exist ".venv\Scripts\python.exe" (
    echo [2/3] Virtual environment already exists - reusing it.
) else (
    echo [2/3] Creating the virtual environment in .venv ...
    %PYEXE% -m venv .venv
    if errorlevel 1 goto fail
)

echo [3/3] Installing packages (a few minutes the first time)...
".venv\Scripts\python.exe" -m pip install --upgrade pip --disable-pip-version-check
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt --disable-pip-version-check
if errorlevel 1 goto fail

".venv\Scripts\python.exe" -c "import ursina, numpy, PIL, scipy; print('       packages OK')"
if errorlevel 1 goto fail

echo.
echo  Setup finished. Start the game with  run.bat
echo.
if /i not "%~1"=="/quiet" pause
exit /b 0

:fail
echo.
echo [ERROR] Setup did not finish - see the messages above.
echo         (Needs an internet connection; run setup.bat again to retry.)
if /i not "%~1"=="/quiet" pause
exit /b 1

REM ---- sets PYEXE to a usable interpreter (3.12, 3.11, 3.10), or leaves it unset
:find_python
set "PYEXE="
for %%V in (3.12 3.11 3.10) do (
    if not defined PYEXE (
        py -%%V -c "import sys" >nul 2>nul
        if not errorlevel 1 set "PYEXE=py -%%V"
    )
)
if defined PYEXE exit /b 0
for %%P in ("%LocalAppData%\Programs\Python\Python312\python.exe" "%LocalAppData%\Programs\Python\Python311\python.exe" "%LocalAppData%\Programs\Python\Python310\python.exe") do (
    if not defined PYEXE if exist %%P set "PYEXE=%%~P"
)
if defined PYEXE (
    set "PYEXE="%PYEXE%""
    exit /b 0
)
python -c "import sys; sys.exit(0 if (3,10)<=sys.version_info[:2]<=(3,12) else 1)" >nul 2>nul
if not errorlevel 1 set "PYEXE=python"
exit /b 0
