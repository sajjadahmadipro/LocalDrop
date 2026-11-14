@echo off
rem LocalDrop desktop window. Double-click this, or make a shortcut to it.
setlocal
cd /d "%~dp0"

set "CMD="
rem pythonw first: no console window, and the window outlives whatever launched
rem it. "py -3 -w" is NOT valid - the py launcher forwards -w to python.exe,
rem which rejects it, so that spelling silently never opens a window.
where pythonw >nul 2>nul
if not errorlevel 1 set "CMD=start "" pythonw localdrop\launcher.py"

if not defined CMD (
    where py >nul 2>nul
    if not errorlevel 1 set "CMD=start "" py -3 localdrop\launcher.py"
)

if not defined CMD (
    where python >nul 2>nul
    if not errorlevel 1 set "CMD=start "" python localdrop\launcher.py"
)

if not defined CMD (
    echo LocalDrop needs Python 3.8 or newer and none was found.
    echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
    pause
    exit /b 1
)

%CMD% %*
endlocal
