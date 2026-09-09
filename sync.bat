@echo off
rem Pull latest changes from GitHub into this folder (fast-forward only).
rem Run this before launching the app to get edits made in the browser.
cd /d "%~dp0"
git pull --ff-only
if errorlevel 1 (
  echo.
  echo [sync] Pull failed. Possible cause: local uncommitted changes.
  echo [sync] Run "git status --short" to inspect, commit or stash them, then retry.
  pause
  exit /b 1
)
echo.
echo [sync] Up to date with origin/main.
