"""Почтовые ящики запуска и почтовые действия шагов.

Ящик умеет жить дольше одного шага: `MailHub` держит текущий клиент, отдаёт
адрес в переменные сценария и выполняет почтовые действия (`mail_create`,
`mail_wait_code`, `mail_wait_link`, `mail_open_link`, `mail_refresh`) в любом
месте списка шагов — до формы, между полями или после отправки.

Сервисов три вида: встроенный браузерный `tmail`, встроенный `mail.tm` по API
и любой добавленный пользователем (`mail_services/*.json`, исполняется
`universal/scripted_mail.py`).
"""
from __future__ import annotations

import secrets
from typing import Any, Callable

from core.tmail_web import TmailWebClient
from providers.eirouter_mail import MailTmClient
from universal import mail_services
from universal.mail_text import DEFAULT_LINK_KEYWORDS, LINK_REGEX, keywords_of
from universal.scripted_mail import ScriptedMailClient

# Совместимость: старый код и тексты ошибок ссылались на эти имена.
MAIL_ALIASES = mail_services.ALIASES
normalize_mail_service = mail_services.normalize


def mail_choices() -> tuple[str, ...]:
    """Что можно выбрать при запуске: встроенные, свои и алиасы."""
    return mail_services.choices()


def mail_service_ids() -> tuple[str, ...]:
    """Канонические имена сервисов без алиасов."""
    return mail_services.known_ids()


def random_login() -> str:
    return "user" + secrets.token_hex(5)


async def create_mailbox(service: str, context, client, log: Callable[[str], None],
                         username: str | None = None):
    """Создать ящик выбранного сервиса; для `none` вернуть None."""
    service = normalize_mail_service(service)
    if service in ("", mail_services.NO_MAIL):
        return None
    if service == "mail.tm":
        mailbox = MailTmClient(client, log)
        await mailbox.create_mailbox()
        return mailbox
    if service == "tmail":
        mailbox = TmailWebClient(LINK_REGEX, DEFAULT_LINK_KEYWORDS, log)
        await mailbox.open(context)
        await mailbox.create_mailbox(username or random_login())
        return mailbox
    described = mail_services.get(service)
    if described is None:
        known = ", ".join(mail_service_ids())
        raise ValueError(f"Неизвестный почтовый сервис: {service} (известны: {known})")
    mailbox = ScriptedMailClient(described, log)
    await mailbox.open(context)
    await mailbox.create_mailbox(username)
    return mailbox


class MailHub:
    """Текущий почтовый ящик запуска и операции над ним."""

    def __init__(self, context, client, default_service: str,
                 log: Callable[[str], None], variables: dict[str, Any]) -> None:
        self.context = context
        self.client = client
        self.default_service = normalize_mail_service(default_service)
        self.log = log
        self.variables = variables
        self.mailbox: Any = None
        self.service: str = mail_services.NO_MAIL

    # ---- состояние ----
    @property
    def address(self) -> str:
        return getattr(self.mailbox, "address", "") or ""

    def _publish(self) -> None:
        """Отдать адрес в переменные сценария, чтобы {email} стал актуальным."""
        self.variables["email"] = self.address
        self.variables["_mailbox"] = self.mailbox

    def require(self):
        if self.mailbox is None:
            raise RuntimeError("Ящик не создан: добавьте шаг mail_create или "
                               "выберите почтовый сервис при запуске")
        return self.mailbox

    # ---- операции ----
    async def create(self, service: str | None = None, username: str | None = None):
        target = normalize_mail_service(service or "") or self.default_service
        if target in ("", mail_services.NO_MAIL):
            raise RuntimeError("mail_create: не выбран почтовый сервис")
        await self.close_current()
        self.mailbox = await create_mailbox(target, self.context, self.client,
                                            self.log, username)
        self.service = target
        self._publish()
        self.log(f"[mail] Ящик {self.address} ({target})")
        return self.mailbox

    async def ensure(self, service: str | None = None):
        if self.mailbox is None:
            await self.create(service)
        return self.mailbox

    async def close_current(self) -> None:
        page = getattr(self.mailbox, "page", None)
        if page is not None:
            try:
                await page.close()
            except Exception:  # вкладка уже закрыта вместе с контекстом
                pass
        self.mailbox = None

    async def wait_code(self, length: int = 6, timeout: float = 180,
                        sender_hint: str | None = None) -> str:
        mailbox = self.require()
        hint = (sender_hint or "").strip()
        if self.service == "mail.tm":
            # У Mail.tm фильтр отправителя — подстрока; пустая берёт любое письмо.
            code = await mailbox.wait_for_code(length=length, timeout=timeout, sender_hint=hint)
        else:
            code = await mailbox.wait_for_code(length=length, timeout=timeout,
                                               sender_hint=hint or None)
        self.log(f"[mail] Код из письма получен ({len(code)} зн.)")
        return code

    async def wait_link(self, timeout: float = 300, keywords: Any = None) -> str:
        mailbox = self.require()
        words = keywords_of(keywords)
        if hasattr(mailbox, "link_keywords") and keywords:
            mailbox.link_keywords = words
        if self.service == "mail.tm":
            link = await mailbox.wait_for_link(timeout=timeout, keywords=words)
        elif isinstance(mailbox, ScriptedMailClient):
            link = await mailbox.wait_for_link(timeout=timeout,
                                               keywords=words if keywords else None)
        else:
            link = await mailbox.wait_for_link(timeout=timeout)
        self.log("[mail] Ссылка подтверждения получена")
        return link

    async def refresh(self) -> None:
        mailbox = self.require()
        refresh = getattr(mailbox, "refresh", None)
        if refresh is None:
            return
        await refresh()
