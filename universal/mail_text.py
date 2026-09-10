"""Разбор письма: адрес ящика, код подтверждения, ссылка подтверждения.

Один модуль на все почтовые клиенты — и на встроенные, и на добавленные
пользователем (`mail_services/*.json`). Правила здесь текстовые: они работают
одинаково по видимому тексту письма, по HTML и по телу сетевого ответа.
"""
from __future__ import annotations

import html
import re
from typing import Any, Iterable

EMAIL_REGEX = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
LINK_REGEX = r"https?://[^\s\"'<>]+"

DEFAULT_LINK_KEYWORDS = ("verify", "confirm", "activate", "подтверд")
# Ссылки политик и соглашений не являются ссылкой подтверждения.
DEFAULT_LINK_EXCLUDE = ("legal", "privacy", "terms", "policy", "agreement", "cookie",
                        "unsubscribe")
# Слова, после которых в письме идёт код (en/ru/zh).
DEFAULT_CODE_KEYWORDS = ("code", "otp", "pin", "verification", "verify", "passcode",
                         "код", "验证码", "验证")
# Служебные адреса самого сервиса: адресом ящика они не бывают.
ADDRESS_BLACKLIST = ("support", "admin", "noreply", "no-reply", "example", "abuse",
                     "postmaster", "info@", "help@")

# Число — часть CSS-значения (padding:12 / 0x1f / #123456), а не код.
_CODE_NOISE_BEFORE = re.compile(
    r"(?:#|0x)\s*$"
    r"|(?:width|height|padding|margin|font|border|size|top|left|right|bottom"
    r"|rgb|rgba|line|spacing|radius|weight)[^A-Za-z0-9]{0,4}$",
    re.I)
_CODE_NOISE_AFTER = re.compile(r"^\s*(?:px|pt|em|rem|%|deg)", re.I)


def keywords_of(value: Any, default: Iterable[str] = DEFAULT_LINK_KEYWORDS) -> tuple[str, ...]:
    """Слова-признаки: строка через запятую, список или пусто (тогда default)."""
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        parts = [str(part).strip() for part in value]
    else:
        parts = []
    cleaned = tuple(part.lower() for part in parts if part)
    return cleaned or tuple(str(item).lower() for item in default)


def strip_markup(text: str) -> str:
    """Снять script/style, теги и HTML-эскейпы.

    Числа из разметки (width:600px, #123456, Message-ID: <483920@mail>) живут
    в атрибутах и заголовках — после снятия тегов они не конкурируют с кодом
    из текста письма.
    """
    text = re.sub(r"(?is)<(script|style)\b.*?</\1\s*>", " ", text or "")
    text = re.sub(r"(?s)<[^>]{1,400}>", " ", text)
    return html.unescape(text)


def clean_url(url: str) -> str:
    """Из HTML ссылка приходит с &amp; вместо & — сервер получал мусорный
    параметр amp;… и страница подтверждения ломалась."""
    url = html.unescape(url or "")
    return url.rstrip(").,;'\"]").rstrip("&")


def pick_code(sources: Iterable[str], length: int = 6,
              keywords: Iterable[str] = DEFAULT_CODE_KEYWORDS) -> str | None:
    r"""Код длины `length` из текстов письма.

    Границы — (?<!\d)…(?!\d): \b срабатывает и внутри «12345678», отдавая
    случайный срез длинного числа. Приоритет у числа, перед которым стоит
    слово вида «code»; иначе берём первое изолированное число, не похожее на
    CSS-значение.
    """
    pattern = re.compile(r"(?<!\d)(\d{%d})(?!\d)" % int(length))
    words = tuple(str(word).lower() for word in keywords) or DEFAULT_CODE_KEYWORDS
    fallback: str | None = None
    for text in sources:
        clean = strip_markup(text)
        low = clean.lower()
        for match in pattern.finditer(clean):
            before = low[max(0, match.start() - 80):match.start()]
            after = low[match.end():match.end() + 8]
            if _CODE_NOISE_BEFORE.search(before) or _CODE_NOISE_AFTER.match(after):
                continue
            if any(word in before for word in words):
                return match.group(1)
            if fallback is None:
                fallback = match.group(1)
    return fallback


def pick_link(candidates: Iterable[str], keywords: Iterable[str] = DEFAULT_LINK_KEYWORDS,
              exclude: Iterable[str] = DEFAULT_LINK_EXCLUDE) -> str | None:
    """Первая ссылка со словом-признаком, кроме политик и отписок."""
    words = tuple(str(word).lower() for word in keywords) or DEFAULT_LINK_KEYWORDS
    banned = tuple(str(word).lower() for word in exclude)
    for raw in candidates:
        url = clean_url(raw)
        if not url:
            continue
        lowered = url.lower()
        if any(word in lowered for word in banned):
            continue
        if any(word in lowered for word in words):
            return url
    return None


def find_links(text: str) -> list[str]:
    """Ссылки из текста/HTML/JSON: в JSON «/» экранируют как «\\/»."""
    body = text or ""
    found: list[str] = []
    for source in (body, body.replace("\\/", "/").replace("\\u0026", "&")):
        found.extend(match.group(0) for match in re.finditer(LINK_REGEX, source))
    return list(dict.fromkeys(found))


def find_address(text: str) -> str | None:
    """Адрес ящика из текста страницы, кроме служебных адресов сервиса."""
    for candidate in re.findall(EMAIL_REGEX, strip_markup(text or "")):
        if not any(word in candidate.lower() for word in ADDRESS_BLACKLIST):
            return candidate
    return None


def is_address(value: str) -> bool:
    return bool(re.fullmatch(EMAIL_REGEX, (value or "").strip()))
