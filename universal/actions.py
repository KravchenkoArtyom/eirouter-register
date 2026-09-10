"""Каталог действий сценария — единый источник правды для всего проекта.

Раннер (`universal.runner`) исполняет ровно эти действия, валидатор
(`universal.scenarios`) проверяет обязательные поля, а WebUI строит из этих же
описаний форму шага. Новое действие добавляется здесь и в раннер — править
HTML/JS не нужно.
"""
from __future__ import annotations

from typing import Any

# Подстановки, доступные в текстовых значениях шагов.
PLACEHOLDERS = ("{email}", "{login}", "{username}", "{password}",
                "{password_confirm}", "{code}", "{link}")

GROUPS = (
    {"id": "form", "label": "Форма"},
    {"id": "wait", "label": "Ожидание"},
    {"id": "dom", "label": "DOM"},
    {"id": "mail", "label": "Почта"},
    {"id": "guard", "label": "Капча и уведомления"},
)

# Поля, которые WebUI добавляет к форме шага сам: таймаут — только действиям
# с селектором, заметка — всем (раннер её игнорирует).
TIMEOUT_FIELD = {"name": "timeout_ms", "label": "Таймаут ожидания элемента, мс",
                 "type": "number", "placeholder": "10000"}
NOTE_FIELD = {"name": "note", "label": "Заметка (для себя, на запуск не влияет)",
              "type": "text"}

DOM_OPERATIONS = ("remove", "set_attribute", "set_html", "insert_html",
                  "append_html", "prepend_html", "set_text")

_FORCE = {"name": "force", "label": "force — действовать сквозь перекрытия",
          "type": "checkbox"}

ACTIONS: dict[str, dict[str, Any]] = {
    # ---- форма ----
    "fill": {
        "group": "form", "label": "fill — вписать значение",
        "hint": "Мгновенно подставляет значение в поле.",
        "selector": "required",
        "fields": [{"name": "value", "label": "Значение", "type": "text",
                    "placeholder": "{email}"}],
    },
    "type": {
        "group": "form", "label": "type — набрать по символам",
        "hint": "Печатает значение посимвольно: подходит полям с валидацией на ввод.",
        "selector": "required",
        "fields": [{"name": "value", "label": "Значение", "type": "text",
                    "placeholder": "{password}"},
                   {"name": "delay_ms", "label": "Задержка между символами, мс",
                    "type": "number", "placeholder": "35"}],
    },
    "click": {
        "group": "form", "label": "click — клик",
        "selector": "required", "fields": [_FORCE],
    },
    "check": {
        "group": "form", "label": "check — включить чекбокс",
        "selector": "required", "fields": [_FORCE],
    },
    "uncheck": {
        "group": "form", "label": "uncheck — снять чекбокс",
        "selector": "required", "fields": [_FORCE],
    },
    "select": {
        "group": "form", "label": "select — выбрать в списке",
        "selector": "required",
        "fields": [{"name": "value", "label": "Значение опции", "type": "text",
                    "required": True}],
    },
    "press": {
        "group": "form", "label": "press — нажать клавишу",
        "selector": "required",
        "fields": [{"name": "key", "label": "Клавиша", "type": "text",
                    "required": True, "placeholder": "Enter"}],
    },
    # ---- ожидание и навигация ----
    "wait": {
        "group": "wait", "label": "wait — пауза",
        "selector": "none",
        "fields": [{"name": "seconds", "label": "Секунды", "type": "number",
                    "required": True, "placeholder": "2"}],
    },
    "wait_visible": {
        "group": "wait", "label": "wait_visible — дождаться элемента",
        "selector": "required", "fields": [],
    },
    "wait_url": {
        "group": "wait", "label": "wait_url — дождаться адреса",
        "selector": "none",
        "fields": [{"name": "url", "label": "Шаблон адреса", "type": "text",
                    "required": True, "placeholder": "**/login**"},
                   {"name": "timeout_ms", "label": "Таймаут, мс", "type": "number",
                    "placeholder": "30000"}],
    },
    "goto": {
        "group": "wait", "label": "goto — открыть адрес",
        "hint": "Переходит по адресу в текущей вкладке; поддерживает подстановки.",
        "selector": "none",
        "fields": [{"name": "url", "label": "URL", "type": "text", "required": True,
                    "placeholder": "https://site.test/register"}],
    },
    # ---- DOM ----
    "dom": {
        "group": "dom", "label": "dom — правка страницы",
        "hint": "Меняет DOM только в текущем запуске: удалить баннер, вставить фрагмент.",
        "selector": "required",
        "fields": [
            {"name": "operation", "label": "Операция", "type": "select", "required": True,
             "options": [{"value": op, "label": op} for op in DOM_OPERATIONS]},
            {"name": "name", "label": "Имя атрибута (set_attribute)", "type": "text"},
            {"name": "value", "label": "Значение атрибута (set_attribute)", "type": "text"},
            {"name": "html", "label": "HTML (set_html / *_html)", "type": "textarea"},
            {"name": "text", "label": "Текст (set_text)", "type": "text"},
        ],
    },
    # ---- капча, задержки, уведомления ----
    "captcha_wait": {
        "group": "guard", "label": "captcha_wait — ждать решения капчи",
        "hint": "Останавливает сценарий, зовёт человека и ждёт, пока проверка "
                "уйдёт с экрана. Капчу программа не решает.",
        "selector": "none",
        "fields": [
            {"name": "timeout", "label": "Сколько ждать, с", "type": "number",
             "placeholder": "300"},
            {"name": "message", "label": "Текст уведомления", "type": "text",
             "placeholder": "Появилась капча — решите её в окне браузера"},
        ],
    },
    "wait_random": {
        "group": "guard", "label": "wait_random — случайная пауза",
        "hint": "Пауза со случайной длительностью в заданных границах.",
        "selector": "none",
        "fields": [
            {"name": "min_seconds", "label": "От, с", "type": "number", "required": True,
             "placeholder": "1"},
            {"name": "max_seconds", "label": "До, с", "type": "number", "required": True,
             "placeholder": "4"},
        ],
    },
    "notify": {
        "group": "guard", "label": "notify — уведомление",
        "hint": "Пишет сообщение в лог и показывает его в панели: удобно "
                "помечать места, где нужен человек.",
        "selector": "none",
        "fields": [
            {"name": "message", "label": "Сообщение", "type": "text", "required": True,
             "placeholder": "Проверьте телефон"},
            {"name": "level", "label": "Важность", "type": "select",
             "options": [{"value": "info", "label": "обычное"},
                         {"value": "warn", "label": "важное"},
                         {"value": "ok", "label": "успех"}]},
        ],
    },
    # ---- почта ----
    "mail_create": {
        "group": "mail", "label": "mail_create — создать ящик",
        "hint": "Создаёт ящик посреди сценария и обновляет {email}.",
        "selector": "none",
        "fields": [
            {"name": "service", "label": "Сервис", "type": "select",
             "source": "mail_services",
             "options": [{"value": "", "label": "как выбрано в запуске"},
                         {"value": "tmail", "label": "tmail (браузер)"},
                         {"value": "mail.tm", "label": "Mail.tm (API)"}]},
            {"name": "username", "label": "Имя ящика (пусто — случайное)", "type": "text"},
        ],
    },
    "mail_wait_code": {
        "group": "mail", "label": "mail_wait_code — ждать код из письма",
        "hint": "Кладёт код в переменную (по умолчанию {code}).",
        "selector": "none",
        "fields": [
            {"name": "length", "label": "Длина кода", "type": "number", "placeholder": "6"},
            {"name": "timeout", "label": "Таймаут, с", "type": "number", "placeholder": "180"},
            {"name": "sender_hint", "label": "Фильтр отправителя", "type": "text"},
            {"name": "var", "label": "Переменная", "type": "text", "placeholder": "code"},
        ],
    },
    "mail_wait_link": {
        "group": "mail", "label": "mail_wait_link — ждать ссылку из письма",
        "hint": "Кладёт ссылку подтверждения в переменную (по умолчанию {link}).",
        "selector": "none",
        "fields": [
            {"name": "timeout", "label": "Таймаут, с", "type": "number", "placeholder": "300"},
            {"name": "keywords", "label": "Слова в ссылке (через запятую)", "type": "text",
             "placeholder": "verify, confirm"},
            {"name": "var", "label": "Переменная", "type": "text", "placeholder": "link"},
        ],
    },
    "mail_open_link": {
        "group": "mail", "label": "mail_open_link — открыть ссылку из письма",
        "hint": "Берёт ссылку из переменной или дожидается письма и открывает её.",
        "selector": "none",
        "fields": [
            {"name": "target", "label": "Где открыть", "type": "select",
             "options": [{"value": "same_tab", "label": "в текущей вкладке"},
                         {"value": "new_tab", "label": "в новой вкладке"}]},
            {"name": "close_after", "label": "Закрыть вкладку после открытия",
             "type": "checkbox"},
            {"name": "timeout", "label": "Таймаут ожидания письма, с", "type": "number",
             "placeholder": "300"},
            {"name": "keywords", "label": "Слова в ссылке (через запятую)", "type": "text",
             "placeholder": "verify, confirm"},
            {"name": "var", "label": "Переменная со ссылкой", "type": "text",
             "placeholder": "link"},
        ],
    },
    "mail_refresh": {
        "group": "mail", "label": "mail_refresh — обновить ящик",
        "hint": "Для браузерного tmail жмёт Refresh; для Mail.tm ничего не делает.",
        "selector": "none", "fields": [],
    },
    # ---- совместимость: старое имя mail_wait_code ----
    "wait_email_code": {
        "group": "mail", "label": "wait_email_code — устаревшее имя mail_wait_code",
        "selector": "none", "hidden": True,
        "fields": [
            {"name": "length", "label": "Длина кода", "type": "number"},
            {"name": "timeout", "label": "Таймаут, с", "type": "number"},
            {"name": "sender_hint", "label": "Фильтр отправителя", "type": "text"},
        ],
    },
}

MAIL_ACTIONS = tuple(name for name, spec in ACTIONS.items() if spec["group"] == "mail")
GUARD_ACTIONS = tuple(name for name, spec in ACTIONS.items() if spec["group"] == "guard")
# Что можно исполнять на любой странице — в том числе на странице почтового
# сервиса, у которой своего ящика нет.
PAGE_ACTIONS = tuple(name for name, spec in ACTIONS.items() if spec["group"] != "mail")
VISIBLE_ACTIONS = tuple(name for name, spec in ACTIONS.items() if not spec.get("hidden"))


def needs_selector(action: str) -> bool:
    return ACTIONS.get(action, {}).get("selector") == "required"


def required_fields(action: str) -> tuple[str, ...]:
    spec = ACTIONS.get(action, {})
    return tuple(field["name"] for field in spec.get("fields", []) if field.get("required"))


def catalog() -> dict[str, Any]:
    """Описание для WebUI: группы, действия, служебные поля и подстановки."""
    return {
        "groups": [dict(group) for group in GROUPS],
        "placeholders": list(PLACEHOLDERS),
        "timeout_field": dict(TIMEOUT_FIELD),
        "note_field": dict(NOTE_FIELD),
        "actions": [dict(spec, name=name) for name, spec in ACTIONS.items()],
    }
