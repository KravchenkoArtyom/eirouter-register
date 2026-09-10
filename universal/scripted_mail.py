"""Почтовый клиент по описанию из `mail_services/*.json`.

Клиент ничего не знает про конкретный сайт: он открывает страницу почты и
исполняет её шаги теми же действиями, что и обычный сценарий регистрации
(`fill`, `click`, `wait_visible`…). Дальше остаётся текстовая работа — прочитать
адрес, дождаться кода или ссылки, — и она живёт в `universal/mail_text.py`.
"""
from __future__ import annotations

import asyncio
import secrets
import time
from typing import Any
from collections.abc import Callable

from universal import mail_text

DEFAULT_POLL = 3.0
MAX_POLL = 60.0


def random_login() -> str:
    return "user" + secrets.token_hex(5)


def _first_selector_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    return []


class ScriptedMailClient:
    """Ящик на стороннем сайте, описанный шагами.

    Совпадает по методам с `TmailWebClient`, поэтому `MailHub` работает с ним
    без исключений: `open`, `create_mailbox`, `refresh`, `wait_for_code`,
    `wait_for_link`.
    """

    def __init__(self, service: dict[str, Any], log: Callable[[str], None]) -> None:
        self.service = service or {}
        self.log = log
        self.page = None
        self.address: str | None = None
        self.name = str(self.service.get("name") or "custom")
        link = self.service.get("link") if isinstance(self.service.get("link"), dict) else {}
        self.link_keywords = mail_text.keywords_of(link.get("keywords"))
        self.link_exclude = mail_text.keywords_of(link.get("exclude"),
                                                  mail_text.DEFAULT_LINK_EXCLUDE)

    # ---- служебное ----
    @property
    def poll_interval(self) -> float:
        try:
            value = float(self.service.get("poll_interval") or DEFAULT_POLL)
        except (TypeError, ValueError):
            value = DEFAULT_POLL
        return min(max(value, 0.5), MAX_POLL)

    def _address_config(self) -> dict[str, Any]:
        config = self.service.get("address")
        return config if isinstance(config, dict) else {}

    def _code_config(self) -> dict[str, Any]:
        config = self.service.get("code")
        return config if isinstance(config, dict) else {}

    def _runner(self, variables: dict[str, Any]):
        # Локальный импорт: `universal.runner` сам импортирует почту, и на
        # верхнем уровне получился бы круг.
        from universal.runner import ScenarioRunner
        return ScenarioRunner(self.page, variables, self.log, None)

    async def _run_section(self, section: str, variables: dict[str, Any] | None = None) -> bool:
        steps = self.service.get(section)
        if not isinstance(steps, list) or not steps:
            return False
        runner = self._runner(dict(variables or {}))
        await runner.run(steps, f"mail.{section}")
        return True

    # ---- жизненный цикл ----
    async def open(self, context) -> None:
        url = str(self.service.get("url") or "").strip()
        if not url:
            raise ValueError(f"Почтовый сервис {self.name}: не указан адрес страницы")
        self.page = await context.new_page()
        try:
            # Разрешение выдаём только странице почты: сайту регистрации
            # читать буфер обмена незачем.
            await context.grant_permissions(["clipboard-read", "clipboard-write"],
                                            origin=url)
        except Exception:  # не Chromium или права уже выданы
            pass
        await self.page.goto(url, wait_until="domcontentloaded", timeout=120_000)

    async def create_mailbox(self, username: str | None = None,
                             timeout: float | None = None) -> str:
        login = (username or "").strip() or random_login()
        await self._run_section("create", {"username": login, "login": login})
        limit = timeout if timeout is not None else self._address_config().get("timeout", 60)
        self.address = await self._wait_address(float(limit or 60))
        self.log(f"[mail:{self.name}] адрес прочитан: {self.address}")
        return self.address

    async def refresh(self) -> bool:
        return await self._run_section("refresh")

    async def open_message(self) -> bool:
        try:
            return await self._run_section("open_message")
        except Exception as error:  # писем ещё нет — не повод падать
            self.log(f"[mail:{self.name}] письмо пока не открылось ({type(error).__name__})")
            return False

    # ---- адрес ящика ----
    async def _wait_address(self, timeout: float = 60) -> str:
        deadline = time.monotonic() + max(timeout, 1)
        while time.monotonic() < deadline:
            address = await self._read_address()
            if address:
                return address
            await asyncio.sleep(1)
        raise TimeoutError(f"почтовый сервис {self.name}: адрес ящика не прочитан "
                           f"за {int(timeout)} с")

    async def _read_address(self) -> str | None:
        config = self._address_config()
        source = str(config.get("source") or "value")
        selectors = _first_selector_list(config.get("selectors"))
        if source == "page" or not selectors:
            return await self._address_from_page()
        for selector in selectors:
            try:
                target = self.page.locator(selector).first
                if not await target.count():
                    continue
                if source == "clipboard":
                    value = await self._address_from_clipboard(target)
                elif source == "attribute":
                    value = await target.get_attribute(str(config.get("attribute") or "value"))
                elif source == "text":
                    value = await target.inner_text(timeout=2000)
                else:
                    value = await target.input_value(timeout=2000)
            except Exception:
                continue
            value = (value or "").strip()
            if mail_text.is_address(value):
                return value
            found = mail_text.find_address(value)
            if found:
                return found
        return await self._address_from_page()

    async def _address_from_clipboard(self, target) -> str:
        await target.click(timeout=2000)
        await asyncio.sleep(0.4)
        text = await self.page.evaluate(
            "() => navigator.clipboard.readText().catch(() => '')")
        return str(text or "")

    async def _address_from_page(self) -> str | None:
        try:
            return mail_text.find_address(await self.page.inner_text("body"))
        except Exception:
            return None

    # ---- текст письма ----
    async def _page_text(self) -> str:
        parts = []
        for frame in self.page.frames:
            try:
                parts.append(await frame.locator("body").inner_text(timeout=2000))
            except Exception:
                continue
        return "\n".join(parts)

    async def _frames_html(self) -> list[str]:
        chunks = []
        for frame in self.page.frames:
            try:
                chunks.append(await frame.content())
            except Exception:
                continue
        return chunks

    # ---- ожидание письма ----
    async def wait_for_code(self, length: int = 6, timeout: float = 180,
                            sender_hint: str | None = None) -> str:
        config = self._code_config()
        length = int(config.get("length") or length or 6)
        keywords = mail_text.keywords_of(config.get("keywords"),
                                         mail_text.DEFAULT_CODE_KEYWORDS)
        hint = (sender_hint or config.get("sender_hint") or "").strip().lower()
        deadline = time.monotonic() + max(timeout, 5)
        announced = False
        while time.monotonic() < deadline:
            await self.refresh()
            await asyncio.sleep(self.poll_interval)
            opened = await self.open_message()
            if opened and not announced:
                self.log(f"[mail:{self.name}] письмо открыто, читаю текст")
                announced = True
            if opened:
                await asyncio.sleep(1.5)  # телу письма нужно время загрузиться
            text = await self._page_text()
            if hint and hint not in text.lower():
                continue
            code = mail_text.pick_code([text], length, keywords)
            if code:
                return code
        raise TimeoutError(f"почтовый сервис {self.name}: код ({length} цифр) "
                           f"не пришёл за {int(timeout)} с")

    async def wait_for_link(self, timeout: float = 300, keywords: Any = None) -> str:
        words = mail_text.keywords_of(keywords) if keywords else self.link_keywords
        deadline = time.monotonic() + max(timeout, 5)
        while time.monotonic() < deadline:
            await self.refresh()
            await asyncio.sleep(self.poll_interval)
            opened = await self.open_message()
            if opened:
                await asyncio.sleep(1.5)
            candidates: list[str] = []
            for chunk in await self._frames_html():
                candidates.extend(mail_text.find_links(chunk))
            candidates.extend(mail_text.find_links(await self._page_text()))
            link = mail_text.pick_link(candidates, words, self.link_exclude)
            if link:
                return link
        raise TimeoutError(f"почтовый сервис {self.name}: ссылка подтверждения "
                           f"не пришла за {int(timeout)} с")
