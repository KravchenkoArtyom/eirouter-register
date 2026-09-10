@echo off
rem Подтянуть изменения из GitHub в эту папку (только fast-forward).
cd /d "%~dp0.."
git pull --ff-only
if errorlevel 1 (
  echo.
  echo [sync] Обновление не удалось. Возможная причина: локальные незакоммиченные правки.
  echo [sync] Проверьте "git status --short", закоммитьте или спрячьте их и повторите.
  pause
  exit /b 1
)
echo.
echo [sync] Актуально с origin/main.
