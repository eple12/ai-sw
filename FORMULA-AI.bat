@echo off
REM FORMULA-AI one-click: double-click to set everything up (first run) and play.
REM   first run : setup.bat finds Python 3.10-3.12 (installs 3.12 if there is none),
REM               creates .venv and installs requirements.txt
REM   every run : starts the game.   Options:  FORMULA-AI.bat --track Spa --laps 5
setlocal
cd /d "%~dp0"
title FORMULA-AI
call run.bat %*
endlocal & exit /b %ERRORLEVEL%
