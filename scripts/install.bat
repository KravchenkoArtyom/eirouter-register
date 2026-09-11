@echo off
rem Установка и обновление регистратора одной командой.
rem
rem Скрипт делает и то, и другое: если проект рядом уже есть — обновляет его,
rem если нет — клонирует с GitHub. Дальше ставит зависимости в .venv,
rem докачивает браузеры Playwright и прогоняет тесты.
rem
rem Запуск: install.bat [--skip-tests]
rem Файл можно положить куда угодно — проект он ищет рядом с собой.
setlocal
chcp 65001 >nul
title Универсальный регистратор: установка и обновление

set "NEW_URL=https://github.com/KravchenkoArtyom/universal-autoregister.git"
set "OLD_URL=https://github.com/KravchenkoArtyom/eirouter-register.git"
set "SKIP_TESTS="
if /I "%~1"=="--skip-tests" set "SKIP_TESTS=1"

rem ---- Git ----
where git >nul 2>&1
if errorlevel 1 (
    echo [!] Git не найден. Поставьте его с https://git-scm.com/download/win и запустите снова.
    goto fail
)

rem ---- Живой адрес репозитория: новое имя, иначе прежнее ----
rem GIT_TERMINAL_PROMPT=0 — чтобы проверка не зависла на вопросе о пароле.
set "GIT_TERMINAL_PROMPT=0"
set "URL=%NEW_URL%"
git ls-remote --exit-code -h "%NEW_URL%" >nul 2>&1
if errorlevel 1 set "URL=%OLD_URL%"
set "GIT_TERMINAL_PROMPT="
echo [1/6] Репозиторий: %URL%

rem ---- Где лежит проект ----
set "ROOT="
if exist "%~dp0..\.git" set "ROOT=%~dp0.."
if not defined ROOT if exist "%~dp0.git" set "ROOT=%~dp0."
if not defined ROOT if exist "%~dp0universal-autoregister\.git" set "ROOT=%~dp0universal-autoregister"
if not defined ROOT if exist "%~dp0eirouter-register\.git" set "ROOT=%~dp0eirouter-register"

if not defined ROOT (
    echo [2/6] Клонирую в "%~dp0universal-autoregister"
    git clone "%URL%" "%~dp0universal-autoregister"
    if errorlevel 1 goto fail
    set "ROOT=%~dp0universal-autoregister"
) else (
    echo [2/6] Нашёл проект: "%ROOT%"
)

cd /d "%ROOT%"
if errorlevel 1 goto fail

rem ---- Обновление файлов ----
git remote set-url origin "%URL%"
git fetch --prune origin
if errorlevel 1 (
    echo [!] Не удалось связаться с GitHub. Проверьте интернет и доступ к репозиторию.
    goto fail
)

git diff --quiet HEAD
if errorlevel 1 (
    echo.
    echo [!] В папке изменены файлы проекта — останавливаюсь, чтобы их не потерять:
    git status --short
    echo.
    echo     Спрятать свои правки:     git stash push -m "before update"
    echo     Вернуть после обновления: git stash pop
    goto fail
)

git checkout -q main
if errorlevel 1 echo [i] Переключиться на main не вышло — обновляю текущую ветку.

git merge --ff-only origin/main
if errorlevel 1 (
    echo.
    echo [!] Быстрое обновление невозможно: в папке есть свои коммиты.
    echo     Посмотрите "git log --oneline origin/main..HEAD" и решите, что с ними делать.
    goto fail
)
echo [3/6] Файлы проекта обновлены.

rem ---- Остатки прежних версий ----
if exist "legacy" rmdir /s /q "legacy"
if exist "pytest.ini" del /q "pytest.ini"

rem ---- Окружение Python ----
set "PY=%CD%\.venv\Scripts\python.exe"
if exist "%PY%" (
    echo [4/6] Виртуальное окружение .venv на месте.
) else (
    set "BOOT="
    where py >nul 2>&1
    if not errorlevel 1 set "BOOT=py -3"
    if not defined BOOT (
        where python >nul 2>&1
        if not errorlevel 1 set "BOOT=python"
    )
    if not defined BOOT (
        echo [!] Python 3.11 или новее не найден: https://www.python.org/downloads/
        goto fail
    )
    echo [4/6] Создаю виртуальное окружение .venv
    %BOOT% -m venv .venv
    if errorlevel 1 goto fail
)

echo [5/6] Ставлю зависимости из requirements-dev.txt
"%PY%" -m pip install --upgrade pip >nul
"%PY%" -m pip install -r requirements-dev.txt
if errorlevel 1 goto fail

"%PY%" -m playwright install chromium
if errorlevel 1 (
    echo [!] Не удалось скачать Chromium для Playwright.
    goto fail
)
"%PY%" -m playwright install chrome
if errorlevel 1 echo [i] Google Chrome не поставился сам. Если он уже установлен — всё в порядке.

rem ---- Проверка ----
if defined SKIP_TESTS (
    echo [6/6] Тесты пропущены по ключу --skip-tests.
) else (
    echo [6/6] Прогоняю тесты — это пара минут.
    "%PY%" -m pytest
    if errorlevel 1 (
        echo [!] Тесты прошли не полностью — смотрите строки FAILED выше.
    ) else (
        echo [ok] Тесты прошли.
    )
)

echo.
echo Готово. Папка проекта: "%CD%"
echo Запуск панели: scripts\webui.bat
echo Панель сама напечатает ссылку с токеном и откроет её в браузере.
echo.
pause
exit /b 0

:fail
echo.
echo Работа прервана.
pause
exit /b 1
