@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -m providers.eirouter --interactive
    goto finished
)

where py >nul 2>&1
if not errorlevel 1 (
    py -3 -m providers.eirouter --interactive
    goto finished
)
if exist "%LocalAppData%\Python\pythoncore-3.14-64\python.exe" (
    "%LocalAppData%\Python\pythoncore-3.14-64\python.exe" -m providers.eirouter --interactive
    goto finished
)
where python >nul 2>&1
if not errorlevel 1 (
    python -m providers.eirouter --interactive
    goto finished
)
echo Python 3 was not found. Install Python and the project requirements.
pause
exit /b 1

:finished
set "eirouter_exit_code=%errorlevel%"
echo.
pause
exit /b %eirouter_exit_code%
