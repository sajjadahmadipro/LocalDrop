@echo off
rem LocalDrop in a console window. Same server as LocalDrop.bat, but you can see
rem the log and press Ctrl+C. Use this when the launcher says something failed.
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"

if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 set "PY=python"
)

if not defined PY (
    echo LocalDrop needs Python 3.8 or newer and none was found.
    echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
    pause
    exit /b 1
)

%PY% -m localdrop %*
if errorlevel 1 pause
endlocal
