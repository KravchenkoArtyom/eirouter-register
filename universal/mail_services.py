"""Почтовые сервисы: встроенные и добавленные пользователем.

Встроенных два — браузерный `tmail` и `mail.tm` по API. Любой другой сервис
описывается JSON-файлом в `mail_services/`: адрес страницы, шаги создания
ящика, шаги обновления и открытия письма, правила чтения адреса, кода и
ссылки. Такой сервис исполняет `universal/scripted_mail.py` теми же
действиями, что и обычный сценарий, поэтому селекторы для него можно снимать
инспектором.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from universal.actions import PAGE_ACTIONS
from universal.step_check import check_steps

ROOT = Path(__file__).resolve().parent.parent
SERVICES_DIR = ROOT / "mail_services"
TRASH_DIR = SERVICES_DIR / "_trash"

# wibucrypto — домен браузерного tmail-клиента, поэтому это алиас, а не сервис.
ALIASES = {"wibucrypto": "tmail"}
NO_MAIL = "none"
STEP_SECTIONS = ("create", "refresh", "open_message")
ADDRESS_SOURCES = ("value", "text", "attribute", "clipboard", "page")

BUILTIN: dict[str, dict[str, Any]] = {
    NO_MAIL: {"id": NO_MAIL, "label": "Без почты", "kind": "none",
              "hint": "Регистрация без ящика: письма не ждём."},
    "tmail": {"id": "tmail", "label": "tmail (браузер)", "kind": "builtin",
              "url": "https://tmail.wibucrypto.pro/mailbox",
              "hint": "Готовый браузерный клиент tmail.wibucrypto.pro."},
    "mail.tm": {"id": "mail.tm", "label": "Mail.tm (API)", "kind": "builtin",
                "url": "https://api.mail.tm",
                "hint": "Ящик по API, без вкладки браузера — быстрее и стабильнее."},
}

DEFAULT_ADDRESS = {"source": "value", "selectors": [], "attribute": "", "timeout": 60}
DEFAULT_CODE = {"length": 6, "keywords": ""}
DEFAULT_LINK = {"keywords": "verify, confirm", "exclude": "privacy, terms, unsubscribe"}

TEMPLATES: dict[str, dict[str, Any]] = {
    "blank": {"label": "Пустой — опишу шаги сам", "data": {}},
    "button": {
        "label": "Кнопка «Создать» и поле имени",
        "data": {
            "address": {"source": "value", "selectors": ["input[type=text]"], "timeout": 60},
            "create": [
                {"action": "click", "selectors": ["button:has-text('Создать')",
                                                  "button:has-text('Create')"]},
                {"action": "fill", "selectors": ["#user", "input[name=user]"],
                 "value": "{username}"},
                {"action": "click", "selectors": ["#create", "button[type=submit]"]},
            ],
            "refresh": [{"action": "click", "selectors": ["button:has-text('Обновить')",
                                                          "button:has-text('Refresh')"]}],
            "open_message": [{"action": "click", "selectors": [".mail-item", ".message-item"],
                              "timeout_ms": 2000}],
        },
    },
    "instant": {
        "label": "Адрес выдаётся сразу при открытии",
        "data": {
            "address": {"source": "value", "selectors": ["#mail", "input[readonly]"],
                        "timeout": 60},
            "create": [{"action": "wait_visible", "selectors": ["#mail", "input[readonly]"]}],
            "refresh": [{"action": "click", "selectors": ["button:has-text('Refresh')"]}],
            "open_message": [{"action": "click", "selectors": ["tr.message", ".mail-item"],
                              "timeout_ms": 2000}],
        },
    },
    "copy": {
        "label": "Адрес по кнопке «Копировать»",
        "data": {
            "address": {"source": "clipboard", "selectors": [".btn_copy",
                                                             "button:has-text('Копировать')"],
                        "timeout": 60},
            "create": [{"action": "wait_visible", "selectors": [".btn_copy"]}],
            "refresh": [{"action": "click", "selectors": ["button:has-text('Refresh')"]}],
            "open_message": [{"action": "click", "selectors": [".mail-item"],
                              "timeout_ms": 2000}],
        },
    },
}


# ---- имена ----
def normalize(service: str) -> str:
    """Привести имя сервиса к каноническому (`wibucrypto` -> `tmail`)."""
    value = (service or "").strip().lower()
    return ALIASES.get(value, value)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ_.-]+", "-", (name or "").strip()).strip("-_.")
    return slug.lower()[:60]


# ---- файлы ----
def ensure_dir() -> Path:
    SERVICES_DIR.mkdir(exist_ok=True)
    return SERVICES_DIR


def service_files() -> list[Path]:
    ensure_dir()
    return [path for path in sorted(SERVICES_DIR.glob("*.json"))
            if not path.name.startswith("_")]


def read(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Почтовый сервис {Path(path).name}: корень должен быть объектом")
    return data


def id_of(path: Path, data: dict[str, Any]) -> str:
    name = data.get("name")
    return str(name).strip() if isinstance(name, str) and name.strip() else path.stem


def load_all() -> list[dict[str, Any]]:
    """Описания пользовательских сервисов с добавленным полем `_path`."""
    items: list[dict[str, Any]] = []
    for path in service_files():
        try:
            data = read(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        data["_path"] = path
        data["name"] = id_of(path, data)
        items.append(data)
    return items


def find(name: str) -> tuple[Path, dict[str, Any]]:
    target = normalize(name)
    for path in service_files():
        data = read(path)
        if target in (normalize(id_of(path, data)), path.stem.lower(), path.name.lower()):
            data["name"] = id_of(path, data)
            return path, data
    raise KeyError(name)


def get(name: str) -> dict[str, Any] | None:
    """Описание пользовательского сервиса или None, если такого файла нет."""
    try:
        _, data = find(name)
    except (KeyError, OSError, ValueError):
        return None
    return data


def save(path: Path, data: dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: value for key, value in data.items() if not key.startswith("_")}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)
    return path


def create(name: str, url: str = "", template: str = "blank",
           copy_from: str | None = None, label: str = "") -> tuple[Path, dict[str, Any]]:
    slug = slugify(name)
    if not slug:
        raise ValueError("Имя почтового сервиса не может быть пустым")
    if slug in BUILTIN:
        raise ValueError(f"Имя {slug} занято встроенным сервисом")
    ensure_dir()
    path = SERVICES_DIR / f"{slug}.json"
    if path.exists():
        raise FileExistsError(f"Почтовый сервис {path.name} уже существует")
    if copy_from:
        _, source = find(copy_from)
        data = {key: value for key, value in source.items() if not key.startswith("_")}
        data["name"] = slug
        data["label"] = label.strip() or f"{source.get('label', copy_from)} (копия)"
        if url:
            data["url"] = url.strip()
    else:
        if template not in TEMPLATES:
            raise ValueError(f"Неизвестная заготовка: {template}")
        base = json.loads(json.dumps(TEMPLATES[template]["data"]))
        data = {"name": slug, "label": label.strip() or name.strip(), "url": url.strip(),
                "poll_interval": 3,
                "address": dict(DEFAULT_ADDRESS, **base.get("address", {})),
                "code": dict(DEFAULT_CODE), "link": dict(DEFAULT_LINK)}
        for section in STEP_SECTIONS:
            data[section] = base.get(section, [])
    save(path, data)
    return path, data


def delete(name: str) -> Path:
    """Убрать описание в `_trash/` — восстановить можно вручную."""
    path, _ = find(name)
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    target = TRASH_DIR / f"{path.stem}_{stamp}.json"
    os.replace(path, target)
    return target


# ---- список для интерфейса и валидации ----
def summaries() -> list[dict[str, Any]]:
    """Встроенные и пользовательские сервисы одним списком."""
    items = [dict(spec) for spec in BUILTIN.values()]
    for data in load_all():
        path: Path = data["_path"]
        items.append({
            "id": data["name"],
            "label": str(data.get("label") or data["name"]),
            "kind": "custom",
            "url": str(data.get("url", "")),
            "file": path.name,
            "steps": sum(len(data.get(section, []) or []) for section in STEP_SECTIONS),
            "hint": f"Свой сервис: {data.get('url', 'адрес не указан')}",
        })
    return items


def custom_ids() -> tuple[str, ...]:
    return tuple(data["name"] for data in load_all())


def known_ids() -> tuple[str, ...]:
    """Всё, что можно выбрать: встроенные + пользовательские."""
    return tuple(BUILTIN) + custom_ids()


def choices() -> tuple[str, ...]:
    """Знакомые имена вместе с алиасами — для сообщений об ошибках."""
    return known_ids() + tuple(ALIASES)


def is_known(service: str) -> bool:
    value = normalize(service)
    return bool(value) and value in known_ids()


def is_custom(service: str) -> bool:
    return normalize(service) in custom_ids()


# ---- валидация описания ----
def validate(data: Any) -> list[str]:
    """Человекочитаемые ошибки описания сервиса; пустой список — всё хорошо."""
    if not isinstance(data, dict):
        return ["Корень должен быть JSON-объектом"]
    errors: list[str] = []
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("name: обязательное поле")
    elif normalize(name) in BUILTIN:
        errors.append(f"name: {name} — встроенный сервис, выберите другое имя")
    url = data.get("url")
    if not isinstance(url, str) or not url.strip():
        errors.append("url: адрес страницы почты обязателен")
    elif not url.strip().startswith(("http://", "https://")):
        errors.append("url: должен начинаться с http:// или https://")
    address = data.get("address")
    if address is not None:
        if not isinstance(address, dict):
            errors.append("address: должен быть объектом")
        else:
            source = str(address.get("source") or "value")
            if source not in ADDRESS_SOURCES:
                errors.append("address.source — " + " | ".join(ADDRESS_SOURCES))
            selectors = address.get("selectors")
            if source != "page" and not (isinstance(selectors, list) and any(
                    isinstance(item, str) and item.strip() for item in selectors)):
                errors.append("address.selectors: нужен хотя бы один селектор "
                              "(или source=page — искать по тексту страницы)")
            if source == "attribute" and not str(address.get("attribute") or "").strip():
                errors.append("address.attribute: укажите имя атрибута")
    for section in STEP_SECTIONS:
        if section in data and data[section] is not None:
            check_steps(data[section], section, errors, allowed=PAGE_ACTIONS)
    code = data.get("code")
    if isinstance(code, dict) and code.get("length") is not None:
        try:
            length = int(code["length"])
        except (TypeError, ValueError):
            length = 0
        if not 3 <= length <= 12:
            errors.append("code.length: 3..12")
    return errors
