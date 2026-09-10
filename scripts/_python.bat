@echo off
rem Определяет интерпретатор в переменной PY: сначала .venv, затем py -3, затем python.
if exist "%~dp0..\.venv\Scripts\python.exe" (
    set "PY=%~dp0..\.venv\Scripts\python.exe"
    exit /b 0
)
where py >nul 2>&1
if not errorlevel 1 (
    set "PY=py -3"
    exit /b 0
)
where python >nul 2>&1
if not errorlevel 1 (
    set "PY=python"
    exit /b 0
)
echo Python 3 не найден. Установите Python и зависимости из requirements.txt.
exit /b 1
