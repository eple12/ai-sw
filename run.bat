@echo off
REM FORMULA-AI launcher. Double-click to open the start menu and pick a circuit.
REM The first time (no .venv yet) it runs setup.bat for you.
REM To skip the menu:  run.bat --track Spa --laps 5
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo First run: setting things up...
    call setup.bat /quiet
    if errorlevel 1 (
        pause
        exit /b 1
    )
)

".venv\Scripts\python.exe" run.py %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo [exit code %RC%] - something went wrong.
    pause
)
endlocal
