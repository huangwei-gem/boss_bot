@echo off
REM ===================================================================
REM Keep this file ASCII-only. Measured 2026-10-23 on this machine:
REM once the console is on codepage 65001, cmd mis-advances through a
REM UTF-8 .bat that contains Chinese ANYWHERE - even inside REM lines.
REM SYMPTOMS SEEN: "set PYTHONUTF8=1" executed as 'YTHONUTF8', fragments of
REM comments ran as commands ("'lai, bing hui du...' is not recognized"),
REM and "goto" landed on the wrong label, so the launcher died at step 2/5
REM and the panel never started.
REM All Chinese messages and all decisions live in
REM tools\start_accounts.py (UTF-8, launched with PYTHONUTF8=1).
REM ===================================================================
title BOSS Zhipin Bot
cd /d "%~dp0"
set PYTHONUTF8=1

where python >nul 2>&1
if not errorlevel 1 goto run
echo Python was not found. Install Python 3.10+ first:
echo   https://www.python.org/downloads/
goto end

:run
REM No kill_port here on purpose: it force-kills whatever owns the panel port.
REM The panel already has a single-instance guard, so the launcher only
REM checks whether someone answers on that port and never touches it.
python tools\start_accounts.py --launch

:end
echo.
pause
