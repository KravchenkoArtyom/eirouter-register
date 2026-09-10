@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
call "%~dp0_python.bat" || (pause & exit /b 1)
echo Открываю панель: http://127.0.0.1:8765
start "" http://127.0.0.1:8765
%PY% -m webui %*
pause
