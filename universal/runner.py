"""Исполнение сценария регистрации в браузере.

Специфика сайта живёт в `universal_scenarios/*.json`, здесь — только движок.
Раннер не обходит CAPTCHA и другие проверки доступа: он замечает проверку,
ставит сценарий на паузу, зовёт человека (`universal/captcha.py`) и ждёт, пока
проверка уйдёт с экрана. Задержки между шагами и между аккаунтами задаёт
`universal/pacing.py`.
"""
from __future__ import annotations

import asyncio
import secrets
import string
import uuid
from pathlib import Path
from typing import Any, Callable

import httpx
from playwright.async_api import Error as PlaywrightError, Page, async_playwright

from core.adspower import AdsPowerProfiles
from core.navigation import goto_with_retry
from core.storage import AccountStore
from universal.actions import GUARD_ACTIONS, MAIL_ACTIONS
from universal.captcha import CaptchaGuard, CaptchaSettings
from universal.control import RunControl
from universal.mail import MailHub, normalize_mail_service, random_login
from universal.pacing import Pacing
from universal.scenarios import SECTIONS, VERIFICATION_SECTIONS, has_action

# После этих действий страница обычно меняется — там и появляется капча.
CHECK_AFTER = ("click", "press", "goto", "select", "mail_open_link")


def random_password() -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    chars = [secrets.choice(string.ascii_lowercase), secrets.choice(string.ascii_uppercase),
             secrets.choice(string.digits), secrets.choice("!@#$%")]
    chars.extend(secrets.choice(alphabet) for _ in range(16))
    return "".join(chars)


def format_value(value: Any, variables: dict[str, Any]) -> Any:
    return value.format_map(_SafeVars(variables)) if isinstance(value, str) else value


class _SafeVars(dict):
    """Неизвестная подстановка остаётся текстом, а не рушит шаг KeyError-ом."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _int(step: dict[str, Any], name: str, default: int) -> int:
    try:
        return int(step[name])
    except (KeyError, TypeError, ValueError):
        return default


def _float(step: dict[str, Any], name: str, default: float) -> float:
    try:
        return float(step[name])
    except (KeyError, TypeError, ValueError):
        return default


class ScenarioRunner:
    """Выполняет список шагов на странице.

    Почтовые действия (`mail_*`) идут через `MailHub`, поэтому письмо можно
    ждать в любой момент: до формы, между полями или после отправки.
    """

    def __init__(self, page: Page, variables: dict[str, Any],
                 log: Callable[[str], None], mail: MailHub | None = None,
                 guard: CaptchaGuard | None = None, pacing: Pacing | None = None,
                 control: RunControl | None = None) -> None:
        self.page = page
        self.variables = variables
        self.log = log
        self.mail = mail
        self.guard = guard
        self.pacing = pacing
        self.control = control

    # ---- поиск элемента ----
    def _selectors(self, step: dict[str, Any]) -> list[str]:
        selector = step.get("selector") or step.get("selectors")
        if isinstance(selector, str):
            return [selector]
        if isinstance(selector, list) and all(isinstance(item, str) for item in selector):
            return selector
        raise ValueError("Действию нужен selector или selectors")

    async def locator(self, step: dict[str, Any]):
        last_error = None
        for selector in self._selectors(step):
            target = self.page.locator(format_value(selector, self.variables)).first
            try:
                await target.wait_for(state="visible", timeout=_int(step, "timeout_ms", 10_000))
                return target
            except PlaywrightError as error:
                last_error = error
        raise RuntimeError(f"Ни один селектор не найден: {self._selectors(step)}") from last_error

    def _hub(self, action: str) -> MailHub:
        if self.mail is None:
            raise RuntimeError(f"{action}: почтовые действия недоступны в этом запуске")
        return self.mail

    # ---- шаги ----
    async def run(self, steps: list[dict[str, Any]], section: str = "steps") -> None:
        for number, step in enumerate(steps, 1):
            action = step.get("action")
            if not isinstance(action, str):
                raise ValueError(f"{section}: шаг {number} без действия")
            where = f"{section} {number}/{len(steps)} ({action})"
            if self.control is not None and self.control.should_stop:
                raise RuntimeError("Запуск остановлен пользователем")
            if self.pacing is not None:
                await self.pacing.before_step(action)
            await self._guard_check(where)
            if action in GUARD_ACTIONS:
                await self._run_guard(action, step, where)
            elif action in MAIL_ACTIONS:
                await self._run_mail(action, step)
            else:
                await self._run_page(action, step)
            if action in CHECK_AFTER:
                await self._guard_check(where + " → после действия")
            note = f" — {step['note']}" if step.get("note") else ""
            self.log(f"[scenario] {section} {number}/{len(steps)}: {action} готово{note}")

    async def _guard_check(self, where: str) -> None:
        """Капча перед шагом: кликать сквозь проверку бессмысленно."""
        if self.guard is None:
            return
        await self.guard.check(self.page, where=where)

    async def _run_guard(self, action: str, step: dict[str, Any], where: str) -> None:
        if action == "captcha_wait":
            message = str(step.get("message") or "")
            if message and self.control is not None:
                self.control.notice(message, level="warn")
            timeout = _float(step, "timeout", 300)
            if self.guard is not None:
                await self.guard.check(self.page, where=where, force_wait=True,
                                       timeout=timeout)
            else:
                guard = CaptchaGuard(CaptchaSettings(timeout=timeout), self.control, self.log)
                await guard.check(self.page, where=where, force_wait=True, timeout=timeout)
        elif action == "wait_random":
            low = _float(step, "min_seconds", 1)
            high = _float(step, "max_seconds", max(low, 3))
            low, high = sorted((max(low, 0), max(high, 0)))
            pause = secrets.SystemRandom().uniform(low, high)
            self.log(f"[scenario] пауза {pause:.1f} с")
            await asyncio.sleep(pause)
        elif action == "notify":
            message = str(format_value(step.get("message", ""), self.variables))
            level = str(step.get("level") or "info")
            if self.control is not None:
                self.control.notice(message, level=level)
            else:
                self.log(f"[notify] {message}")
        else:
            raise ValueError(f"Неподдерживаемое действие: {action}")

    async def _run_page(self, action: str, step: dict[str, Any]) -> None:
        if action in {"fill", "type"}:
            target = await self.locator(step)
            value = str(format_value(step.get("value", step.get("text", "")), self.variables))
            if action == "type":
                await target.press_sequentially(value, delay=_int(step, "delay_ms", 0))
            else:
                await target.fill(value)
        elif action == "check":
            target = await self.locator(step)
            if not await target.is_checked():
                await target.check(force=bool(step.get("force", False)))
        elif action == "uncheck":
            await (await self.locator(step)).uncheck(force=bool(step.get("force", False)))
        elif action == "click":
            await (await self.locator(step)).click(force=bool(step.get("force", False)))
        elif action == "select":
            await (await self.locator(step)).select_option(
                str(format_value(step["value"], self.variables)))
        elif action == "press":
            await (await self.locator(step)).press(str(step["key"]))
        elif action == "wait":
            await asyncio.sleep(_float(step, "seconds", 1))
        elif action == "wait_visible":
            await self.locator(step)
        elif action == "wait_url":
            await self.page.wait_for_url(str(format_value(step["url"], self.variables)),
                                         timeout=_int(step, "timeout_ms", 30_000))
        elif action == "goto":
            await goto_with_retry(self.page, str(format_value(step["url"], self.variables)),
                                  log=self.log)
        elif action == "dom":
            await self._run_dom(step)
        else:
            raise ValueError(f"Неподдерживаемое действие сценария: {action}")

    async def _run_dom(self, step: dict[str, Any]) -> None:
        target = await self.locator(step)
        operation = step.get("operation")
        if operation == "remove":
            await target.evaluate("node => node.remove()")
        elif operation == "set_attribute":
            await target.evaluate("(node, data) => node.setAttribute(data.name, data.value)",
                                  {"name": step["name"],
                                   "value": str(format_value(step.get("value", ""), self.variables))})
        elif operation == "set_html":
            await target.evaluate("(node, html) => node.innerHTML = html",
                                  str(format_value(step.get("html", ""), self.variables)))
        elif operation in {"insert_html", "append_html", "prepend_html"}:
            html = str(format_value(step.get("html", ""), self.variables))
            await target.evaluate(
                """(node, data) => {
                    if (data.operation === 'prepend_html') node.insertAdjacentHTML('afterbegin', data.html);
                    else if (data.operation === 'append_html') node.insertAdjacentHTML('beforeend', data.html);
                    else node.insertAdjacentHTML('afterend', data.html);
                }""", {"operation": operation, "html": html})
        elif operation == "set_text":
            await target.evaluate("(node, value) => node.textContent = value",
                                  str(format_value(step.get("text", ""), self.variables)))
        else:
            raise ValueError(f"Неподдерживаемая операция DOM: {operation}")

    async def _run_mail(self, action: str, step: dict[str, Any]) -> None:
        hub = self._hub(action)
        if action == "mail_create":
            await hub.create(str(step.get("service", "")) or None,
                             username=step.get("username") or None)
        elif action in {"mail_wait_code", "wait_email_code"}:
            variable = str(step.get("var") or "code")
            self.variables[variable] = await hub.wait_code(
                length=_int(step, "length", 6), timeout=_float(step, "timeout", 180),
                sender_hint=step.get("sender_hint"))
        elif action == "mail_wait_link":
            variable = str(step.get("var") or "link")
            self.variables[variable] = await hub.wait_link(
                timeout=_float(step, "timeout", 300), keywords=step.get("keywords"))
        elif action == "mail_open_link":
            await self._open_mail_link(hub, step)
        elif action == "mail_refresh":
            await hub.refresh()
        else:
            raise ValueError(f"Неподдерживаемое почтовое действие: {action}")

    async def _open_mail_link(self, hub: MailHub, step: dict[str, Any]) -> None:
        variable = str(step.get("var") or "link")
        link = self.variables.get(variable)
        if not link:
            link = await hub.wait_link(timeout=_float(step, "timeout", 300),
                                       keywords=step.get("keywords"))
            self.variables[variable] = link
        if str(step.get("target") or "same_tab") == "new_tab":
            page = await self.page.context.new_page()
            try:
                await goto_with_retry(page, str(link), log=self.log)
            finally:
                if step.get("close_after"):
                    await page.close()
        else:
            await goto_with_retry(self.page, str(link), log=self.log)


def scenario_service(scenario: dict[str, Any], service: str) -> str:
    """Сервис запуска: аргумент важнее, подсказка сценария — на случай «авто»."""
    chosen = normalize_mail_service(service)
    if chosen:
        return chosen
    return normalize_mail_service(str(scenario.get("mail", "none"))) or "none"


async def register_one(scenario: dict[str, Any], service: str, profiles, output: Path,
                       proxy: str | None, log: Callable[[str], None],
                       control: RunControl | None = None, pacing: Pacing | None = None,
                       captcha: CaptchaSettings | None = None) -> dict[str, Any]:
    """Зарегистрировать один аккаунт по сценарию и сохранить его в `output`.

    `control` связывает запуск с наблюдателем (панель, консоль): через него
    уходят уведомления о капче и приходит ответ человека. `pacing` — задержки,
    `captcha` — как вести себя при проверке.
    """
    account_id = str(uuid.uuid4())
    secret = random_password()
    login = random_login()
    store = AccountStore(output)
    context = None
    saved = False
    service = scenario_service(scenario, service)
    pacing = pacing or Pacing.from_dict(scenario.get("delays"))
    guard = CaptchaGuard(captcha or CaptchaSettings.from_dict(scenario.get("captcha")),
                         control, log, pacing)
    async with async_playwright() as pw, httpx.AsyncClient(timeout=30, proxy=proxy,
                                                           trust_env=False) as client:
        try:
            context = await profiles.launch(pw, account_id, proxy, log)
            variables: dict[str, Any] = {
                "email": "", "login": login, "username": login,
                "password": secret, "password_confirm": secret, "_mailbox": None,
            }
            mail = MailHub(context, client, service, log, variables)
            # Сценарий с шагом mail_create управляет ящиком сам; иначе ящик
            # создаётся заранее, как раньше.
            if service != "none" and not has_action(scenario, "mail_create"):
                await mail.create(service)
            placeholder = mail.address or login
            store.add(placeholder, scenario.get("name", "universal"), password=secret,
                      status="registration_pending", device_uuid=account_id)
            page = await context.new_page()
            await goto_with_retry(page, scenario["url"], wait_until="domcontentloaded",
                                  attempts=3, log=log)
            runner = ScenarioRunner(page, variables, log, mail, guard, pacing, control)
            # Капча иногда встречает на входе, ещё до первого шага.
            await runner._guard_check("страница открыта")
            for section in SECTIONS:
                if section == "submit":
                    await _run_verification(runner, scenario)
                if scenario.get(section):
                    await runner.run(scenario[section], section)
            account_name = mail.address or placeholder
            if account_name != placeholder:
                store.remove(placeholder)
                store.add(account_name, scenario.get("name", "universal"), password=secret,
                          status="active", device_uuid=account_id)
            else:
                store.set_status(account_name, "active")
            store.save()
            saved = True
            log(f"[universal] Аккаунт {account_name} сохранён в {output}")
            return store.get(account_name) or {}
        finally:
            if context is not None:
                await profiles.close(context)
            if saved and isinstance(profiles, AdsPowerProfiles):
                await profiles.delete_created(account_id, log)


async def _run_verification(runner: ScenarioRunner, scenario: dict[str, Any]) -> None:
    """Блок verification выполняется между steps и submit, как и раньше."""
    verification = scenario.get("verification")
    if not isinstance(verification, dict):
        return
    defaults = {"wait": [{"action": "mail_wait_code"}]}
    for sub in VERIFICATION_SECTIONS:
        steps = verification.get(sub, defaults.get(sub, []))
        if steps:
            await runner.run(steps, f"verification.{sub}")
