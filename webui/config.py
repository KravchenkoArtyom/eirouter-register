"""Настройки панели: задержки, поведение при капче, ротация прокси.

Лежат в `webui.local.json` рядом с проектом (в `.gitignore`), потому что это
настройки конкретной машины, а не часть сценария. Панель читает их при
загрузке и подставляет в форму запуска; запуск может переопределить любое
значение на один раз.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any

from core.proxy_pool import ROTATIONS
from universal.captcha import MODES as CAPTCHA_MODES, CaptchaSettings
from universal.pacing import Pacing
from webui.settings import ROOT

CONFIG_FILE = ROOT / "webui.local.json"
SECTIONS = ("captcha", "delays", "proxy", "notify")

DEFAULTS: dict[str, Any] = {
    "captcha": CaptchaSettings().as_dict(),
    "delays": Pacing().as_dict(),
    "proxy": {"mode": "per_account", "ban_after": 2, "source": "file"},
    "notify": {"sound": True, "desktop": True, "toast": True},
}

_lock = threading.Lock()


def _merge(base: dict[str, Any], patch: Any) -> dict[str, Any]:
    result = dict(base)
    if isinstance(patch, dict):
        for key, value in patch.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                result[key] = _merge(base[key], value)
            elif key in base:
                result[key] = value
    return result


def load() -> dict[str, Any]:
    """Настройки с подставленными значениями по умолчанию."""
    stored: dict[str, Any] = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            stored = data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            stored = {}
    return _merge(DEFAULTS, stored)


def save(patch: Any) -> dict[str, Any]:
    """Дописать переданные разделы и вернуть итоговые настройки."""
    with _lock:
        merged = _merge(load(), patch)
        merged = normalize(merged)
        temporary = CONFIG_FILE.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        os.replace(temporary, CONFIG_FILE)
        return merged


def normalize(data: dict[str, Any]) -> dict[str, Any]:
    """Причесать значения: режимы из списка, числа неотрицательные."""
    result = _merge(DEFAULTS, data)
    captcha = CaptchaSettings.from_dict(result["captcha"]).as_dict()
    delays = Pacing.from_dict(result["delays"]).as_dict()
    proxy = dict(result["proxy"])
    if proxy.get("mode") not in ROTATIONS:
        proxy["mode"] = "per_account"
    try:
        proxy["ban_after"] = max(int(proxy.get("ban_after", 2)), 0)
    except (TypeError, ValueError):
        proxy["ban_after"] = 2
    if proxy.get("source") not in ("file", "list", "single", "direct"):
        proxy["source"] = "file"
    notify = {key: bool(result["notify"].get(key, True)) for key in DEFAULTS["notify"]}
    return {"captcha": captcha, "delays": delays, "proxy": proxy, "notify": notify}


def options() -> dict[str, Any]:
    """Справочник допустимых значений — панель строит из него списки выбора."""
    return {"captcha_modes": list(CAPTCHA_MODES), "rotations": list(ROTATIONS)}
