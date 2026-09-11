@echo off
rem Установка и обновление регистратора одной командой.
rem
rem Скрипт делает и то, и другое: если проект рядом уже есть - обновляет его,
rem если нет - клонирует с GitHub. Дальше ставит зависимости в .venv,
rem докачивает браузеры Playwright и прогоняет тесты.
rem
rem Запуск: install.bat [--skip-tests]
rem Файл можно положить куда угодно - проект он ищет рядом с собой.
rem
rem Стиль намеренно простой: метки и goto вместо многострочных блоков
rem "if ... ( ... )". Внутри такого блока cmd подставляет переменные заранее,
rem поэтому значение, присвоенное там же, читается пустым.
setlocal
chcp 65001 >nul
title Универсальный регистратор: установка и обновление

set "NEW_URL=https://github.com/KravchenkoArtyom/universal-autoregister.git"
set "OLD_URL=https://github.com/KravchenkoArtyom/eirouter-register.git"
set "HOME_DIR=%~dp0"
set "SKIP_TESTS="
set "MIRRORED="
set "PASS_ON="

rem ---- Разбор ключей ----
:args
if "%~1"=="" goto args_done
if /I "%~1"=="--skip-tests" set "SKIP_TESTS=1"
if /I "%~1"=="--skip-tests" set "PASS_ON=%PASS_ON% --skip-tests"
if /I "%~1"=="--mirrored" set "MIRRORED=1"
if /I "%~1"=="--home" set "HOME_DIR=%~2"
shift
goto args
:args_done

rem ---- Работаем из копии в %TEMP% ----
rem git может обновить сам этот файл во время работы, а cmd читает скрипт с
rem диска по ходу дела - поэтому запускаем копию, которую никто не перепишет.
if defined MIRRORED goto start
set "MIRROR=%TEMP%\universal-autoregister-install"
if not exist "%MIRROR%" mkdir "%MIRROR%"
copy /y "%~f0" "%MIRROR%\install.bat" >nul
if errorlevel 1 goto start
call "%MIRROR%\install.bat" --mirrored --home "%HOME_DIR%"%PASS_ON%
exit /b %errorlevel%

:start
rem ---- Git ----
where git >nul 2>&1
if errorlevel 1 goto no_git

rem ---- Живой адрес репозитория: новое имя, иначе прежнее ----
rem GIT_TERMINAL_PROMPT=0 - чтобы проверка не зависла на вопросе о пароле.
set "GIT_TERMINAL_PROMPT=0"
set "URL=%NEW_URL%"
git ls-remote --exit-code -h "%NEW_URL%" >nul 2>&1
if errorlevel 1 set "URL=%OLD_URL%"
set "GIT_TERMINAL_PROMPT="
echo [1/6] Репозиторий: %URL%

rem ---- Где лежит проект ----
set "ROOT="
if exist "%HOME_DIR%..\.git" set "ROOT=%HOME_DIR%.."
if not defined ROOT if exist "%HOME_DIR%.git" set "ROOT=%HOME_DIR%."
if not defined ROOT if exist "%HOME_DIR%universal-autoregister\.git" set "ROOT=%HOME_DIR%universal-autoregister"
if not defined ROOT if exist "%HOME_DIR%eirouter-register\.git" set "ROOT=%HOME_DIR%eirouter-register"
if defined ROOT goto have_root

echo [2/6] Клонирую в "%HOME_DIR%universal-autoregister"
git clone "%URL%" "%HOME_DIR%universal-autoregister"
if errorlevel 1 goto fail
set "ROOT=%HOME_DIR%universal-autoregister"
goto enter_root

:have_root
echo [2/6] Нашёл проект: "%ROOT%"

:enter_root
cd /d "%ROOT%"
if errorlevel 1 goto fail

rem ---- Обновление файлов ----
git remote set-url origin "%URL%"
git fetch --prune origin
if errorlevel 1 goto no_github

git diff --quiet HEAD
if errorlevel 1 goto dirty

git checkout -q main
if errorlevel 1 echo [i] Переключиться на main не вышло - обновляю текущую ветку.

git merge --ff-only origin/main
if errorlevel 1 goto own_commits
echo [3/6] Файлы проекта обновлены.

rem ---- Остатки прежних версий ----
if exist "legacy" rmdir /s /q "legacy"
if exist "pytest.ini" del /q "pytest.ini"

rem ---- Окружение Python ----
set "PY=%CD%\.venv\Scripts\python.exe"
if exist "%PY%" goto venv_ready
call :make_venv
if errorlevel 1 goto no_python
if not exist "%PY%" goto no_python
goto deps

:venv_ready
echo [4/6] Виртуальное окружение .venv на месте.

:deps
echo [5/6] Ставлю зависимости из requirements-dev.txt
"%PY%" -m pip install --upgrade pip >nul
"%PY%" -m pip install -r requirements-dev.txt
if errorlevel 1 goto fail

"%PY%" -m playwright install chromium
if errorlevel 1 goto no_chromium

"%PY%" -m playwright install chrome
if errorlevel 1 echo [i] Google Chrome не поставился сам. Если он уже установлен - всё в порядке.

rem ---- Проверка ----
if defined SKIP_TESTS goto skipped_tests
echo [6/6] Прогоняю тесты - это пара минут.
"%PY%" -m pytest
if errorlevel 1 echo [!] Тесты прошли не полностью - смотрите строки FAILED выше.
if not errorlevel 1 echo [ok] Тесты прошли.
goto done

:skipped_tests
echo [6/6] Тесты пропущены по ключу --skip-tests.

:done
echo.
echo Готово. Папка проекта: "%CD%"
echo Запуск панели: scripts\webui.bat
echo Панель сама напечатает ссылку с токеном и откроет её в браузере.
echo.
pause
exit /b 0

rem ---- Создание .venv: сначала пробуем py -3, затем python ----
:make_venv
echo [4/6] Создаю виртуальное окружение .venv
where py >nul 2>&1
if errorlevel 1 goto venv_python
py -3 -m venv .venv
if not errorlevel 1 exit /b 0
echo [i] py -3 не справился - пробую python.
:venv_python
where python >nul 2>&1
if errorlevel 1 exit /b 1
python -m venv .venv
if not errorlevel 1 exit /b 0
exit /b 1

rem ---- Сообщения об остановках ----
:no_git
echo [!] Git не найден. Поставьте его с https://git-scm.com/download/win и запустите снова.
goto fail

:no_github
echo [!] Не удалось связаться с GitHub. Проверьте интернет и доступ к репозиторию.
goto fail

:dirty
echo.
echo [!] В папке изменены файлы проекта - останавливаюсь, чтобы их не потерять:
git status --short
echo.
echo     Спрятать свои правки:     git stash push -m "before update"
echo     Вернуть после обновления: git stash pop
goto fail

:own_commits
echo.
echo [!] Быстрое обновление невозможно: в папке есть свои коммиты.
echo     Посмотрите "git log --oneline origin/main..HEAD" и решите, что с ними делать.
goto fail

:no_python
echo [!] Не удалось создать .venv. Нужен Python 3.11 или новее: https://www.python.org/downloads/
echo     Проверьте, что в командной строке работает "py -3 --version" или "python --version".
goto fail

:no_chromium
echo [!] Не удалось скачать Chromium для Playwright. Проверьте интернет и запустите снова.
goto fail

:fail
echo.
echo Работа прервана.
pause
exit /b 1
