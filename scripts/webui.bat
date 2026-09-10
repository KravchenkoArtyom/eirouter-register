@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
call "%~dp0_python.bat" || (pause & exit /b 1)
rem Ссылку с токеном печатает и открывает сама панель (webui/server.py).
%PY% -m webui %*
pause
