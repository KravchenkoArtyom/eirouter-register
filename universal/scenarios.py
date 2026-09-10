"""Файлы сценариев: чтение, запись, создание, удаление и валидация.

Каждый сайт — отдельный JSON в `universal_scenarios/`. Имена, начинающиеся с
`_`, служебные и в список не попадают (там же лежит `_trash/` — корзина
удалённых сценариев).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from universal.captcha import MODES as CAPTCHA_MODES
from universal.mail_services import choices as mail_choices, known_ids, normalize
from universal.step_check import check_steps

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS_DIR = ROOT / "universal_scenarios"
TRASH_DIR = SCENARIOS_DIR / "_trash"

SECTIONS = ("steps", "submit", "success")
VERIFICATION_SECTIONS = ("send", "wait", "complete")

TEMPLATES: dict[str, dict[str, Any]] = {
    "blank": {"label": "Пустой", "steps": []},
    "email": {
        "label": "Почта + пароль",
        "steps": [
            {"action": "fill", "selectors": ["input[type=email]", "input[name=email]"],
             "value": "{email}"},
            {"action": "fill", "selectors": ["input[name=password]", "#password"],
             "value": "{password}"},
            {"action": "fill", "selectors": ["input[name=password_confirmation]",
                                             "#password-confirm"], "value": "{password_confirm}"},
            {"action": "check", "selectors": ["input[type=checkbox]"]},
        ],
        "submit": [{"action": "click", "selectors": ["button[type=submit]"]}],
    },
    "login": {
        "label": "Логин + пароль",
        "steps": [
            {"action": "fill", "selectors": ["input[name=username]"], "value": "{login}"},
            {"action": "fill", "selectors": ["input[name=password]"], "value": "{password}"},
            {"action": "fill", "selectors": ["input[name=password2]"],
             "value": "{password_confirm}"},
        ],
        "submit": [{"action": "click", "selectors": ["button[type=submit]"]}],
    },
    "email_code": {
        "label": "Почта + код из письма",
        "steps": [
            {"action": "mail_create", "service": ""},
            {"action": "fill", "selectors": ["input[type=email]"], "value": "{email}"},
            {"action": "fill", "selectors": ["input[name=password]"], "value": "{password}"},
            {"action": "click", "selectors": ["button:has-text('Send code')"]},
            {"action": "mail_wait_code", "length": 6, "timeout": 180},
            {"action": "fill", "selectors": ["input[name=code]"], "value": "{code}"},
        ],
        "submit": [{"action": "click", "selectors": ["button[type=submit]"]}],
    },
    "email_captcha": {
        "label": "Почта + капча вручную",
        "steps": [
            {"action": "mail_create", "service": ""},
            {"action": "fill", "selectors": ["input[type=email]"], "value": "{email}"},
            {"action": "fill", "selectors": ["input[name=password]"], "value": "{password}"},
            {"action": "captcha_wait", "timeout": 300,
             "message": "Появилась капча — решите её в окне браузера"},
        ],
        "submit": [{"action": "click", "selectors": ["button[type=submit]"]}],
        "verification": {"wait": [{"action": "mail_wait_code", "length": 6, "timeout": 180}]},
    },
    "email_link": {
        "label": "Почта + ссылка подтверждения",
        "steps": [
            {"action": "mail_create", "service": ""},
            {"action": "fill", "selectors": ["input[type=email]"], "value": "{email}"},
            {"action": "fill", "selectors": ["input[name=password]"], "value": "{password}"},
        ],
        "submit": [{"action": "click", "selectors": ["button[type=submit]"]}],
        "success": [
            {"action": "mail_open_link", "target": "same_tab", "timeout": 300,
             "keywords": "verify, confirm"},
        ],
    },
}


# ---- чтение ----
def ensure_dir() -> Path:
    SCENARIOS_DIR.mkdir(exist_ok=True)
    return SCENARIOS_DIR


def scenario_files() -> list[Path]:
    ensure_dir()
    return [path for path in sorted(SCENARIOS_DIR.glob("*.json"))
            if not path.name.startswith("_")]


def read(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Сценарий {Path(path).name}: корень должен быть объектом")
    return data


def title_of(path: Path, data: dict[str, Any]) -> str:
    name = data.get("name")
    return str(name) if isinstance(name, str) and name.strip() else path.stem


def load_scenarios(include_hidden: bool = False) -> list[dict[str, Any]]:
    """Все сценарии каталога с добавленным полем `_path`."""
    result: list[dict[str, Any]] = []
    for path in scenario_files():
        data = read(path)
        if data.get("hidden") and not include_hidden:
            continue
        if not isinstance(data.get("url"), str):
            raise ValueError(f"Сценарий {path.name}: нет поля url")
        for section in SECTIONS:
            if section in data and not isinstance(data[section], list):
                raise ValueError(f"Сценарий {path.name}: {section} должен быть списком")
        if data.get("verification") is not None and not isinstance(data["verification"], dict):
            raise ValueError(f"Сценарий {path.name}: verification должен быть объектом")
        data["_path"] = path
        result.append(data)
    return result


def summaries() -> list[dict[str, Any]]:
    """Краткий список для WebUI и выбора в CLI."""
    items = []
    for data in load_scenarios():
        path: Path = data["_path"]
        items.append({
            "name": title_of(path, data),
            "file": path.name,
            "url": data.get("url", ""),
            "mail": data.get("mail", ""),
            "steps": sum(len(data.get(section, [])) for section in SECTIONS),
            "mail_steps": len(mail_steps(data)),
            "has_verification": bool(data.get("verification")),
        })
    return items


def find(name: str) -> tuple[Path, dict[str, Any]]:
    """Найти сценарий по имени или имени файла."""
    for path in scenario_files():
        data = read(path)
        if name in (title_of(path, data), path.stem, path.name):
            return path, data
    raise KeyError(name)


# ---- запись ----
def save(path: Path, data: dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: value for key, value in data.items() if not key.startswith("_")}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)
    return path


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ_-]+", "-", (name or "").strip()).strip("-_.")
    return slug.lower()[:60]


def create(name: str, url: str = "", mail: str = "tmail", template: str = "blank",
           copy_from: str | None = None) -> tuple[Path, dict[str, Any]]:
    """Создать файл нового сценария. Возвращает путь и данные."""
    slug = slugify(name)
    if not slug:
        raise ValueError("Имя сценария не может быть пустым")
    ensure_dir()
    path = SCENARIOS_DIR / f"{slug}.json"
    if path.exists():
        raise FileExistsError(f"Сценарий {path.name} уже существует")
    if copy_from:
        source_path, source = find(copy_from)
        data = {key: value for key, value in source.items() if not key.startswith("_")}
        data["name"] = name.strip()
        if url:
            data["url"] = url.strip()
    else:
        if template not in TEMPLATES:
            raise ValueError(f"Неизвестный шаблон: {template}")
        base = TEMPLATES[template]
        data = {"name": name.strip(), "url": url.strip(),
                "mail": normalize(mail) or "tmail"}
        for section in SECTIONS:
            if section in base:
                data[section] = json.loads(json.dumps(base[section]))
    save(path, data)
    return path, data


def delete(name: str) -> Path:
    """Убрать сценарий в `_trash/` — восстановить можно вручную."""
    path, _ = find(name)
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    target = TRASH_DIR / f"{path.stem}_{stamp}.json"
    os.replace(path, target)
    return target


# ---- разбор шагов ----
def iter_sections(data: dict[str, Any]):
    """Пары (путь, список шагов) по всем секциям, включая verification."""
    for section in SECTIONS:
        if isinstance(data.get(section), list):
            yield section, data[section]
    verification = data.get("verification")
    if isinstance(verification, dict):
        for sub in VERIFICATION_SECTIONS:
            if isinstance(verification.get(sub), list):
                yield f"verification.{sub}", verification[sub]


def mail_steps(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for _, steps in iter_sections(data) for step in steps
            if isinstance(step, dict) and str(step.get("action", "")).startswith(("mail_", "wait_email"))]


def has_action(data: dict[str, Any], action: str) -> bool:
    return any(isinstance(step, dict) and step.get("action") == action
               for _, steps in iter_sections(data) for step in steps)


# ---- валидация ----
def _mail_checks(step: dict[str, Any], where: str, errors: list[str]) -> None:
    """Проверки, которые знает только сценарий: имена сервисов и цель ссылки."""
    action = step.get("action")
    if action == "mail_create":
        service = normalize(str(step.get("service", "")))
        if service and service not in known_ids():
            errors.append(f"{where}: mail_create.service — {' | '.join(known_ids())}")
    if action == "mail_open_link" and step.get("target") not in (None, "", "same_tab", "new_tab"):
        errors.append(f"{where}: mail_open_link.target — same_tab | new_tab")


def _check_steps(steps: Any, path: str, errors: list[str]) -> None:
    check_steps(steps, path, errors, extra=_mail_checks)


def _check_extras(data: dict[str, Any], errors: list[str]) -> None:
    """Необязательные блоки сценария: задержки и поведение при капче."""
    delays = data.get("delays")
    if delays is not None:
        if not isinstance(delays, dict):
            errors.append("delays: должен быть объектом")
        else:
            for key, value in delays.items():
                if not isinstance(value, (int, float)) or value < 0:
                    errors.append(f"delays.{key}: неотрицательное число")
    captcha = data.get("captcha")
    if captcha is not None:
        if not isinstance(captcha, dict):
            errors.append("captcha: должен быть объектом")
        elif captcha.get("mode") is not None and captcha["mode"] not in CAPTCHA_MODES:
            errors.append("captcha.mode — " + " | ".join(CAPTCHA_MODES))


def validate(data: Any) -> list[str]:
    """Список человекочитаемых ошибок; пустой — сценарий валиден."""
    if not isinstance(data, dict):
        return ["Корень должен быть JSON-объектом"]
    errors: list[str] = []
    if "name" in data and (not isinstance(data["name"], str) or not data["name"].strip()):
        errors.append("name: непустая строка")
    if not isinstance(data.get("url"), str) or not data["url"].strip():
        errors.append("url: обязательное поле")
    elif not data["url"].strip().startswith(("http://", "https://")):
        errors.append("url: должен начинаться с http:// или https://")
    if "mail" in data and normalize(str(data["mail"])) not in mail_choices():
        errors.append("mail: " + " | ".join(mail_choices()))
    for section in SECTIONS:
        if section in data:
            _check_steps(data[section], section, errors)
    verification = data.get("verification")
    if verification is not None:
        if not isinstance(verification, dict):
            errors.append("verification: должен быть объектом")
        else:
            for sub in VERIFICATION_SECTIONS:
                if sub in verification:
                    _check_steps(verification[sub], f"verification.{sub}", errors)
    _check_extras(data, errors)
    return errors
