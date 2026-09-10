"""Пути и константы WebUI: всё, что зависит от расположения проекта."""
from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
ROOT = PACKAGE_DIR.parent
STATIC_DIR = PACKAGE_DIR / "static"

OUTPUT_FILE = ROOT / "universal_accounts.json"
ACCOUNT_FILES = ("universal_accounts.json", "eirouter_accounts.json",
                 "wisdomsatan_accounts.json")
ADSPOWER_CONFIG = ROOT / "adspower.local.json"
PROFILES_DIR = ROOT / "universal_profiles"
PROXIES_FILE = ROOT / "proxies.txt"
# Временное хранилище элементов инспектора (в .gitignore).
BUFFER_FILE = ROOT / "webui_elements.json"

MAX_INSPECT_SESSIONS = 3
# Простаивающая сессия инспектора закрывается сама: браузер не должен висеть
# после того, как человек ушёл со страницы.
INSPECT_IDLE_SECONDS = 900

# Логи панели: журнал запусков и артефакты упавших аккаунтов (в .gitignore).
LOGS_DIR = ROOT / "logs"
RUNS_LOG_DIR = LOGS_DIR / "runs"
FAILURES_DIR = LOGS_DIR / "failures"
# Сколько строк лога панель держит в памяти; полный лог лежит в файле запуска.
RUN_LOG_LIMIT = 5000
# Сколько аккаунтов можно регистрировать одновременно в одном запуске.
MAX_RUN_WORKERS = 4
