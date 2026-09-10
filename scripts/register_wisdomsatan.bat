@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
call "%~dp0_python.bat" || (pause & exit /b 1)
%PY% -m providers.wisdomsatan --interactive %*
set "exit_code=%errorlevel%"
echo.
pause
exit /b %exit_code%
