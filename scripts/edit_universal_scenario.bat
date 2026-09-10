@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
call "%~dp0_python.bat" || (pause & exit /b 1)
%PY% -m tools.scenario_editor %*
pause
