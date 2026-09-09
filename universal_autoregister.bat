@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" universal_autoregister.py
    goto finished
)
where py >nul 2>&1
if not errorlevel 1 (
    py -3 universal_autoregister.py
    goto finished
)
where python >nul 2>&1
if not errorlevel 1 (
    python universal_autoregister.py
    goto finished
)
echo Python 3 was not found. Install Python and project requirements.
pause
exit /b 1

:finished
set "exit_code=%errorlevel%"
echo.
pause
exit /b %exit_code%
