@echo off
rem Replaces any existing LocalDrop rule with one that opens the TCP port for
rem devices on this network's own subnet, whatever category (Public or Private)
rem Windows gave the network, and deletes the BLOCK rules Windows created if a
rem firewall prompt was ever cancelled (block beats allow). Double-click it, say
rem yes to the Administrator prompt, done. Running it twice leaves one rule.
rem The launcher's "Allow on this network" button adds the same rule;
rem this file is the fallback when that button is refused or unavailable.
setlocal
cd /d "%~dp0.."

net session >nul 2>nul
if errorlevel 1 (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

set "PY="
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 set "PY=python"
)

if not defined PY (
    echo Could not find Python. Install it from https://www.python.org/downloads/
    echo or type this at an Administrator prompt, replacing PORT with your port:
    echo netsh advfirewall firewall add rule name="LocalDrop" dir=in action=allow protocol=TCP localport=8080 profile=any remoteip=localsubnet enable=yes
    pause
    exit /b 1
)

%PY% -m localdrop.firewall remove
%PY% -m localdrop.firewall add
rem A cancelled "Windows Firewall blocked some features" prompt leaves BLOCK rules
rem for python.exe, and block beats allow. Remove only those auto-created ones.
powershell -NoProfile -Command "Get-NetFirewallRule -Direction Inbound -Action Block -Enabled True | Where-Object { $_.Name -like '*Query User*' } | ForEach-Object { $p = ($_ | Get-NetFirewallApplicationFilter).Program; if ($p -like '*python*') { $_ | Remove-NetFirewallRule; Write-Host ('removed block rule for ' + $p) } }"
echo.
%PY% -m localdrop.firewall status
echo.
pause
endlocal
