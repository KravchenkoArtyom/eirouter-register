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
