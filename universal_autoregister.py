"""Configurable registration runner for ordinary email/password forms.

Site-specific behavior belongs in universal_scenarios/*.json. This runner does
not bypass CAPTCHA or other access controls; interactive browser steps remain
visible to the user.
"""

import argparse
import asyncio
import json
import secrets
import string
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from playwright.async_api import Error as PlaywrightError, Page, TimeoutError as PlaywrightTimeout, async_playwright

from core.adspower import AdsPowerProfiles
from core.browser_profile import BrowserProfiles
from core.navigation import goto_with_retry
from core.proxy_pool import load_list, normalize_proxy
from core.run_logs import run_logged
from core.storage import AccountStore
from core.tmail_web import TmailWebClient
from providers.eirouter_mail import MailTmClient


ROOT = Path(__file__).resolve().parent
SCENARIOS = ROOT / "universal_scenarios"


MAIL_ALIASES = {"wibucrypto": "tmail"}
MAIL_CHOICES = ("none", "tmail", "wibucrypto", "mail.tm")


def normalize_mail_service(service: str) -> str:
    """Привести сервис почты к каноническому имени.

    wibucrypto.pro — домен браузерного tmail-клиента (TmailWebClient),
    поэтому wibucrypto это алиас tmail, а не отдельный сервис.
    """
    return MAIL_ALIASES.get((service or "").strip().lower(), (service or "").strip().lower())


def random_login() -> str:
    return "user" + secrets.token_hex(5)


def random_password() -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    chars = [secrets.choice(string.ascii_lowercase), secrets.choice(string.ascii_uppercase),
             secrets.choice(string.digits), secrets.choice("!@#$%")]
    chars.extend(secrets.choice(alphabet) for _ in range(16))
    return "".join(chars)


def prompt_count() -> int:
    while True:
        try:
            value = int(input("How many accounts do you want to register? ").strip())
            if value > 0:
                return value
        except ValueError:
            pass
        print("Enter a positive whole number.", flush=True)


def prompt_choice(prompt: str, choices: dict[str, str]) -> str:
    while True:
        print(prompt, flush=True)
        for key, label in choices.items():
            print(f"  {key}. {label}", flush=True)
        selected = input("Choice: ").strip() or next(iter(choices))
        if selected in choices:
            return selected
        print("Choose one of the listed options.", flush=True)


def load_scenarios() -> list[dict[str, Any]]:
    SCENARIOS.mkdir(exist_ok=True)
    result = []
    for path in sorted(SCENARIOS.glob("*.json")):
        if path.name.startswith("_"):
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("hidden"):
            continue
        if not isinstance(data, dict) or not isinstance(data.get("url"), str):
            raise ValueError(f"Invalid scenario: {path.name}")
        for section in ("steps", "submit"):
            if section in data and not isinstance(data[section], list):
                raise ValueError(f"Scenario {path.name}: {section} must be a list")
        if data.get("verification") is not None and not isinstance(data["verification"], dict):
            raise ValueError(f"Scenario {path.name}: verification must be an object")
        data["_path"] = path
        result.append(data)
    return result


def format_value(value: Any, variables: dict[str, str]) -> Any:
    return value.format_map(variables) if isinstance(value, str) else value


class ScenarioRunner:
    def __init__(self, page: Page, variables: dict[str, str], log) -> None:
        self.page = page
        self.variables = variables
        self.log = log

    def _selectors(self, step: dict[str, Any]) -> list[str]:
        selector = step.get("selector") or step.get("selectors")
        if isinstance(selector, str):
            return [selector]
        if isinstance(selector, list) and all(isinstance(item, str) for item in selector):
            return selector
        raise ValueError("Each selector-based action needs selector or selectors")

    async def locator(self, step: dict[str, Any]):
        last_error = None
        for selector in self._selectors(step):
            target = self.page.locator(format_value(selector, self.variables)).first
            try:
                await target.wait_for(state="visible", timeout=int(step.get("timeout_ms", 10_000)))
                return target
            except PlaywrightError as error:
                last_error = error
        raise RuntimeError(f"No matching visible selector: {self._selectors(step)}") from last_error

    async def run(self, steps: list[dict[str, Any]]) -> None:
        for number, step in enumerate(steps, 1):
            action = step.get("action")
            if not isinstance(action, str):
                raise ValueError(f"Scenario step {number} has no action")
            if action in {"fill", "type"}:
                target = await self.locator(step)
                value = str(format_value(step.get("value", step.get("text", "")), self.variables))
                if action == "type":
                    await target.press_sequentially(value, delay=int(step.get("delay_ms", 0)))
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
                await (await self.locator(step)).select_option(str(format_value(step["value"], self.variables)))
            elif action == "press":
                await (await self.locator(step)).press(str(step["key"]))
            elif action == "wait":
                await asyncio.sleep(float(step.get("seconds", 1)))
            elif action == "wait_visible":
                await self.locator(step)
            elif action == "wait_url":
                await self.page.wait_for_url(str(format_value(step["url"], self.variables)),
                                             timeout=int(step.get("timeout_ms", 30_000)))
            elif action == "wait_email_code":
                mailbox = self.variables["_mailbox"]
                self.variables["code"] = await mailbox.wait_for_code(
                    length=int(step.get("length", 6)), timeout=float(step.get("timeout", 180)),
                    sender_hint=step.get("sender_hint"))
            elif action == "dom":
                target = await self.locator(step)
                operation = step.get("operation")
                if operation == "remove":
                    await target.evaluate("node => node.remove()")
                elif operation == "set_attribute":
                    await target.evaluate("(node, data) => node.setAttribute(data.name, data.value)",
                                          {"name": step["name"], "value": str(format_value(step["value"], self.variables))})
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
                    raise ValueError(f"Unsupported DOM operation: {operation}")
            else:
                raise ValueError(f"Unsupported scenario action: {action}")
            self.log(f"[scenario] Step {number}/{len(steps)}: {action} complete")


async def create_mailbox(service: str, context, client: httpx.AsyncClient, log):
    service = normalize_mail_service(service)
    if service == "none":
        return None
    if service == "mail.tm":
        mailbox = MailTmClient(client, log)
        await mailbox.create_mailbox()
        return mailbox
    mailbox = TmailWebClient(r"https?://[^\s]+", ("verify",), log)
    await mailbox.open(context)
    await mailbox.create_mailbox(random_login())
    return mailbox


async def register_one(scenario: dict[str, Any], service: str, profiles, output: Path,
                       proxy: str | None, log) -> dict[str, Any]:
    account_id = str(uuid.uuid4())
    secret = random_password()
    login = random_login()
    store = AccountStore(output)
    context = None
    saved = False
    async with async_playwright() as pw, httpx.AsyncClient(timeout=30, proxy=proxy, trust_env=False) as client:
        try:
            context = await profiles.launch(pw, account_id, proxy, log)
            mailbox = await create_mailbox(service, context, client, log)
            variables: dict[str, Any] = {
                "email": mailbox.address if mailbox else "", "login": login, "username": login,
                "password": secret, "password_confirm": secret, "_mailbox": mailbox,
            }
            account_name = mailbox.address if mailbox else login
            store.add(account_name, scenario.get("name", "universal"), password=secret,
                      status="registration_pending", device_uuid=account_id)
            page = await context.new_page()
            await goto_with_retry(page, scenario["url"], wait_until="domcontentloaded", attempts=3, log=log)
            runner = ScenarioRunner(page, variables, log)
            await runner.run(scenario.get("steps", []))
            if scenario.get("verification"):
                await runner.run(scenario["verification"].get("send", []))
                await runner.run(scenario["verification"].get("wait", [{"action": "wait_email_code"}]))
                await runner.run(scenario["verification"].get("complete", []))
            if scenario.get("submit"):
                await runner.run(scenario["submit"])
            if scenario.get("success"):
                await runner.run(scenario["success"])
            store.set_status(account_name, "active")
            store.save()
            saved = True
            log(f"[universal] Registered {account_name}; saved to {output}")
            return store.get(account_name) or {}
        finally:
            if context is not None:
                await profiles.close(context)
            if saved and isinstance(profiles, AdsPowerProfiles):
                await profiles.delete_created(account_id, log)


def main() -> int:
    parser = argparse.ArgumentParser(description="Universal configurable account registrar")
    parser.add_argument("--scenario", type=Path, help="Scenario JSON; interactive selection when omitted")
    parser.add_argument("--count", type=int, help="Account count; interactive selection when omitted")
    parser.add_argument("--mail-service", choices=("none", "tmail", "wibucrypto", "mail.tm"))
    parser.add_argument("--proxy", help="One HTTP CONNECT proxy")
    parser.add_argument("--proxies-file", type=Path, default=ROOT / "proxies.txt")
    parser.add_argument("--direct", action="store_true", help="Disable proxies")
    parser.add_argument("--use-proxies", action="store_true", help="Use --proxies-file without prompting")
    parser.add_argument("--browser", choices=("auto", "chrome", "adspower"), default="auto")
    parser.add_argument("--adspower-config", type=Path, default=ROOT / "adspower.local.json")
    parser.add_argument("--output", type=Path, default=ROOT / "universal_accounts.json")
    args = parser.parse_args()
    scenarios = load_scenarios()
    if not scenarios:
        parser.error(f"No scenario files found in {SCENARIOS}")
    if args.direct:
        proxies = []
    elif args.proxy:
        proxies = [normalize_proxy(args.proxy)]
    elif args.use_proxies:
        proxies = load_list(args.proxies_file)
    else:
        proxies = load_list(args.proxies_file) if prompt_choice(
            "Use proxy?", {"1": "No", "2": f"Yes ({args.proxies_file.name})"}) == "2" else []
    service = normalize_mail_service(args.mail_service or {"1": "none", "2": "tmail", "3": "mail.tm", "4": "wibucrypto"}[prompt_choice(
        "Choose mailbox service:", {"1": "No email", "2": "tmail.wibucrypto.pro (browser)", "3": "Mail.tm (API)", "4": "wibucrypto (alias of tmail)"})])
    if args.scenario:
        scenario = json.loads(args.scenario.read_text(encoding="utf-8"))
    else:
        selected = prompt_choice("Choose site scenario:", {
            str(i + 1): str(item.get("name", item["_path"].stem)) for i, item in enumerate(scenarios)})
        scenario = scenarios[int(selected) - 1]
    if not isinstance(scenario, dict) or not isinstance(scenario.get("url"), str):
        parser.error("Scenario must be a JSON object with a url")
    count = args.count or prompt_count()
    if count < 1:
        parser.error("--count must be positive")
    config = json.loads(args.adspower_config.read_text(encoding="utf-8")) if args.adspower_config.exists() else {}
    use_adspower = args.browser == "adspower" or (args.browser == "auto" and bool(config))
    profile_root = ROOT / "universal_profiles"
    if use_adspower:
        key = config.get("api_key")
        if not key:
            parser.error("AdsPower config has no api_key")
        profiles = AdsPowerProfiles(profile_root, config.get("base_url", "http://127.0.0.1:50325"), key)
    else:
        profiles = BrowserProfiles(profile_root)
    def log(message: str) -> None:
        print(message, flush=True)
    failures = 0
    for index in range(count):
        log(f"[universal] Account {index + 1}/{count}; scenario={scenario.get('name', 'unnamed')}; mail={service}")
        try:
            asyncio.run(register_one(scenario, service, profiles, args.output,
                                     proxies[index % len(proxies)] if proxies else None, log))
        except (RuntimeError, TimeoutError, PlaywrightError, httpx.HTTPError, OSError, ValueError) as error:
            failures += 1
            log(f"[universal] Account {index + 1} failed: {type(error).__name__}: {error}")
            log(f"[universal] Continuing with the next account ({failures} failed so far)")
    if failures:
        log(f"[universal] Finished with {failures}/{count} failed account(s); "
            f"see logs/ for details")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run_logged(main, ROOT / "logs"))
