@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
call "%~dp0_python.bat" || (pause & exit /b 1)
rem Устаревший Tkinter-интерфейс; актуальный — scripts\webui.bat.
%PY% -m legacy.universal_autoregister_gui
