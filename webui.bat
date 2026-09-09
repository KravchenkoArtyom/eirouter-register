@echo off
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (
  set PY=.venv\Scripts\python.exe
) else (
  set PY=py -3
)
echo Opening WebUI at http://127.0.0.1:8765 ...
start "" http://127.0.0.1:8765
%PY% webui.py %*
pause
