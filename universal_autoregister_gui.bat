@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" universal_autoregister_gui.py
    exit /b %errorlevel%
)
where py >nul 2>&1
if not errorlevel 1 (
    py -3 universal_autoregister_gui.py
    exit /b %errorlevel%
)
python universal_autoregister_gui.py
