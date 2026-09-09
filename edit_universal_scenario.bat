@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" universal_scenario_editor.py
    goto finished
)
where py >nul 2>&1
if not errorlevel 1 (
    py -3 universal_scenario_editor.py
    goto finished
)
python universal_scenario_editor.py
:finished
pause
