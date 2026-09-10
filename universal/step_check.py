"""Проверка списка шагов по каталогу действий.

Одна проверка на два хранилища: сценарии сайтов (`universal/scenarios.py`) и
описания почтовых сервисов (`universal/mail_services.py`). Специфика каждого
хранилища добавляется колбэком `extra`, чтобы общие правила не расползались.
"""
from __future__ import annotations

from typing import Any
from collections.abc import Callable, Iterable

from universal.actions import ACTIONS, DOM_OPERATIONS, needs_selector, required_fields

Extra = Callable[[dict[str, Any], str, list[str]], None]


def selectors_of(step: dict[str, Any]) -> list[str]:
    value = step.get("selector") or step.get("selectors")
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item.strip()]
    return []


def check_step(step: Any, where: str, errors: list[str],
               allowed: Iterable[str] | None = None, extra: Extra | None = None) -> None:
    if not isinstance(step, dict):
        errors.append(f"{where}: шаг должен быть объектом")
        return
    action = step.get("action")
    if not isinstance(action, str) or not action:
        errors.append(f"{where}: не указано действие")
        return
    if action not in ACTIONS:
        errors.append(f"{where}: неизвестное действие «{action}»")
        return
    if allowed is not None and action not in set(allowed):
        errors.append(f"{where}: действие {action} здесь недоступно")
        return
    if needs_selector(action) and not selectors_of(step):
        errors.append(f"{where}: действию {action} нужен selector или selectors")
    for field in required_fields(action):
        value = step.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"{where}: у {action} не заполнено поле {field}")
    if action == "dom" and step.get("operation") not in DOM_OPERATIONS:
        errors.append(f"{where}: dom.operation — одно из {', '.join(DOM_OPERATIONS)}")
    if extra is not None:
        extra(step, where, errors)


def check_steps(steps: Any, path: str, errors: list[str],
                allowed: Iterable[str] | None = None, extra: Extra | None = None) -> None:
    if not isinstance(steps, list):
        errors.append(f"{path}: должен быть списком шагов")
        return
    for index, step in enumerate(steps, 1):
        check_step(step, f"{path}[{index}]", errors, allowed, extra)
