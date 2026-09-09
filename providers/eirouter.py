"""eirouter email registration and account-key capture through the website."""

import argparse
import asyncio
import json
import os
import re
import secrets
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from playwright.async_api import (
    BrowserContext, Error as PlaywrightError, Page, Response,
    TimeoutError as PlaywrightTimeout, async_playwright,
)

from core.browser_profile import BrowserProfiles
from core.adspower import AdsPowerProfiles
from core.proxy_pool import load_list, normalize_proxy
from core.recipe_runner import RecipeRunner
from core.recipes import Recipe, load_file
from core.storage import AccountStore
from core.run_logs import run_logged
from core.tmail_web import TmailWebClient
from providers.base import BaseProvider
from providers.eirouter_mail import MailTmClient


class EirouterProvider(BaseProvider):
    name = "eirouter"
    REGISTER_URL = "https://eirouter.ai/register"
    CONSOLE_URL = "https://eirouter.ai/api"
    DEFAULT_BASE_URL = "https://api.eirouter.ai/openai/v1"
    DEFAULT_MODEL = None
    MODELS = ()
    device_headers = ()
    RECIPE_PATH = Path(__file__).resolve().parents[1] / "recipes" / "eirouter.json"
    OUTPUT_PATH = Path(__file__).resolve().parents[1] / "eirouter_accounts.json"
    _browser: BrowserContext | None = None

    def __init__(self, mail_service: str = "tmail", output: Path | None = None,
                 profiles: BrowserProfiles | AdsPowerProfiles | None = None, captcha_timeout: int = 600) -> None:
        self.mail_service = mail_service
        self.output = output or self.OUTPUT_PATH
        self._store: AccountStore | None = None
        self.profiles = profiles or BrowserProfiles(self.output.parent / "profiles")
        self.captcha_timeout = captcha_timeout

    def _records(self) -> AccountStore:
        if self._store is None:
            self._store = AccountStore(self.output)
        return self._store

    @property
    def keys_path(self) -> Path:
        return self.output.with_name(self.output.stem.removesuffix("_accounts") + "_keys.txt")

    def export_keys(self) -> None:
        keys = dict.fromkeys(account["api_key"] for account in self._records().all()
                             if account.get("provider") == self.name and account.get("api_key"))
        if any(not re.fullmatch(r"sk-[A-Za-z0-9_-]{16,}", key) for key in keys):
            raise ValueError("Cannot export an invalid API key")
        temporary = self.keys_path.with_suffix(".txt.tmp")
        temporary.write_text("".join(key + "\n" for key in keys), encoding="utf-8")
        temporary.replace(self.keys_path)

    def _save_key(self, email: str, key: str) -> None:
        store = self._records()
        store.set_api_key(email, key, self.DEFAULT_BASE_URL)
        store.set_status(email, "active")
        store.save()
        self.export_keys()

    async def _finish_profile(self, context: BrowserContext, profile_id: str,
                              credentials_saved: bool, log: Callable[[str], None]) -> None:
        await self.profiles.close(context)
        if credentials_saved and isinstance(self.profiles, AdsPowerProfiles):
            log("[adspower] Credentials saved in JSON and TXT; releasing the created profile")
            await self.profiles.delete_created(profile_id, log)

    async def _wait_cloudflare(self, page: Page, log: Callable[[str], None]) -> None:
        widget = page.locator('.auth-turnstile, .cf-turnstile, iframe[src*="challenges.cloudflare.com"], '
                              'input[name="cf-turnstile-response"]')
        if not await widget.count():
            return
        token = page.locator('input[name="cf-turnstile-response"]')
        if await token.count() and await token.first.input_value():
            return
        log(f"[eirouter] Complete Cloudflare in the browser; waiting up to {self.captcha_timeout} seconds")
        await page.bring_to_front()
        try:
            await page.wait_for_function("""() => {
                const token = document.querySelector('input[name="cf-turnstile-response"]');
                return !!(token && token.value);
            }""", timeout=self.captcha_timeout * 1000)
        except PlaywrightTimeout:
            raise TimeoutError("Cloudflare verification is still pending") from None

    @staticmethod
    async def _check_response(response: Response, step: str) -> None:
        if not response.ok:
            raise RuntimeError(f"eirouter {step}: HTTP {response.status}")
        data = await response.json()
        if not isinstance(data, dict) or data.get("code") != 0:
            raise RuntimeError(f"eirouter {step}: server rejected the request")

    async def _copy_key(self, page: Page) -> str:
        await page.goto(self.CONSOLE_URL, wait_until="domcontentloaded")
        button = page.locator("#panel-keys .account-key-card .akc-key-btn").first
        notice = page.locator('.app-modal__footer button:visible').filter(
            has_text=re.compile(r"^\s*(?:\u6211\u77e5\u9053\u4e86|Got it|I understand)\s*$", re.I)).first
        # Capture this click only, so stale clipboard contents cannot become a key.
        await page.evaluate("""() => {
            window.__eirouterCopiedKey = '';
            window.__eirouterOriginalCopy = navigator.clipboard.writeText.bind(navigator.clipboard);
            navigator.clipboard.writeText = async text => { window.__eirouterCopiedKey = text; };
        }""")
        try:
            deadline = time.monotonic() + 45
            while True:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Timed out opening the account key or dismissing announcements")
                try:
                    # A visible key can still be covered by a queue of announcements.
                    if await notice.is_visible():
                        await notice.click(timeout=1000)
                    elif await button.is_visible():
                        await page.evaluate("() => { window.__eirouterCopiedKey = ''; }")
                        await button.click(timeout=1000)
                        break
                    else:
                        await page.locator('[aria-controls="panel-keys"]').click(timeout=1000)
                except PlaywrightTimeout:
                    pass  # Recheck for announcements that appeared during either click.
                await asyncio.sleep(0.25)
            await page.wait_for_function("() => !!window.__eirouterCopiedKey", timeout=10_000)
            key = await page.evaluate("() => window.__eirouterCopiedKey")
        finally:
            await page.evaluate("""() => {
                navigator.clipboard.writeText = window.__eirouterOriginalCopy;
                delete window.__eirouterOriginalCopy;
                delete window.__eirouterCopiedKey;
            }""")
        if not isinstance(key, str) or not re.fullmatch(r"sk-[A-Za-z0-9_-]{16,}", key):
            raise RuntimeError("eirouter copy button did not return a complete API key")
        return key

    async def register_step(self, log: Callable[[str], None], semi_auto: bool = False,
                            proxy: str | None = None) -> dict[str, object]:
        return await self.register_with_recipe(load_file(self.RECIPE_PATH), log, proxy=proxy)

    async def register_with_recipe(self, recipe: Recipe, log: Callable[[str], None],
                                   proxy: str | None = None) -> dict[str, object]:
        if recipe.provider != self.name:
            raise ValueError("Select the eirouter recipe or the built-in flow")
        if self.mail_service not in {"tmail", "mail.tm"}:
            raise ValueError("Unknown eirouter mail service")
        password = "Ei9!" + secrets.token_urlsafe(20)
        profile_id = str(uuid.uuid4())
        credentials_saved = False
        async with async_playwright() as pw, httpx.AsyncClient(timeout=25, proxy=proxy, trust_env=False) as client:
            context = await self.profiles.launch(pw, profile_id, proxy, log)
            self._browser = context
            context.set_default_timeout(30_000)
            context.set_default_navigation_timeout(120_000)
            try:
                if self.mail_service == "mail.tm":
                    mailbox = MailTmClient(client, log)
                    email = await mailbox.create_mailbox()
                else:
                    mailbox = TmailWebClient(r"https://[^\s]+", ("verify",), log)
                    await mailbox.open(context)
                    email = await mailbox.create_mailbox(MailTmClient.random_username())
                store = self._records()
                store.add(email, self.name, password=password, status="registration_pending",
                          base_url=self.DEFAULT_BASE_URL, device_uuid=profile_id)
                log(f"[eirouter] Credentials saved to {self.output}")
                page = await context.new_page()
                await page.goto(self.REGISTER_URL, wait_until="domcontentloaded")
                await page.locator("#reg-email").press_sequentially(email, delay=35)
                await self._wait_cloudflare(page, log)
                async with page.expect_response(
                    lambda r: urlsplit(r.url).path.endswith("/auth/send-verify-code")
                    and r.request.method == "POST", timeout=30_000,
                ) as sent:
                    await page.locator(".auth-code-send-btn").click()
                await self._check_response(await sent.value, "send code")
                log("[eirouter] Verification code sent; waiting for email")
                # tmail creates a fresh empty mailbox; sender names vary by
                # locale/provider, so rely on the verification-code marker
                # instead of requiring the literal word "eirouter".
                sender_hint = "eirouter" if self.mail_service == "mail.tm" else None
                code = await mailbox.wait_for_code(timeout=180, sender_hint=sender_hint)
                await page.bring_to_front()
                await RecipeRunner(page, context_vars={"code": code, "password": password},
                                   log=log).run(recipe)
                await self._wait_cloudflare(page, log)
                async with page.expect_response(
                    lambda r: urlsplit(r.url).path.endswith("/auth/register")
                    and r.request.method == "POST", timeout=30_000,
                ) as registered:
                    await page.locator(".auth-submit").click()
                await self._check_response(await registered.value, "registration")
                store.set_status(email, "no_key")
                store.save()
                log("[eirouter] Registration confirmed; copying the Default account key")
                try:
                    await page.wait_for_url(lambda url: urlsplit(str(url)).path != "/register")
                    key = await self._copy_key(page)
                except (PlaywrightError, RuntimeError):
                    log("[eirouter] Key capture failed; login and password are saved. Use --resume EMAIL")
                    key = ""
                if key:
                    self._save_key(email, key)
                    credentials_saved = True
                    log(f"[eirouter] Email, password and API key saved to {self.output}")
                return {**store.get(email), "confirmed": True}
            finally:
                self._browser = None
                if not credentials_saved:
                    log("[eirouter] Closing browser after an incomplete attempt; profile retained")
                await self._finish_profile(context, profile_id, credentials_saved, log)

    async def resume_key(self, email: str, log: Callable[[str], None],
                         proxy: str | None = None) -> None:
        store = self._records()
        account = store.get(email)
        if not account:
            raise ValueError("Email is not present in the eirouter credentials file")
        credentials_saved = False
        async with async_playwright() as pw:
            context = await self.profiles.launch(pw, account["device_uuid"], proxy, log)
            try:
                page = await context.new_page()
                await page.goto(self.CONSOLE_URL, wait_until="domcontentloaded")
                await page.locator('[aria-controls="panel-keys"], input[type="email"]').first.wait_for()
                if urlsplit(page.url).path == "/login":
                    await page.locator('input[type="email"]').fill(account["email"])
                    await page.locator('input[type="password"]').fill(account["password"])
                    await self._wait_cloudflare(page, log)
                    await page.locator(".auth-submit").click()
                    await page.wait_for_url(lambda url: urlsplit(str(url)).path != "/login", timeout=30_000)
                key = await self._copy_key(page)
                self._save_key(email, key)
                credentials_saved = True
                log(f"[eirouter] API key saved to {self.output}")
            finally:
                await self._finish_profile(context, account["device_uuid"], credentials_saved, log)

    async def check_browser(self, log: Callable[[str], None], proxy: str | None = None) -> None:
        async with async_playwright() as pw:
            context = await self.profiles.launch(pw, str(uuid.uuid4()), proxy, log)
            try:
                page = await context.new_page()
                await page.goto(self.REGISTER_URL, wait_until="domcontentloaded")
                await page.locator("#reg-email").wait_for()
                log("[browser] Registration page loaded. Checking Cloudflare without creating an account.")
                await self._wait_cloudflare(page, log)
                log("[browser] Page ready; no mailbox or service account was created")
            finally:
                await self.profiles.close(context)

    async def fetch_quota(self, account: dict[str, object]) -> None:
        return None

    def get_headers(self, account: dict[str, object]) -> dict[str, str]:
        return {"Authorization": f"Bearer {account.get('api_key') or account.get('token') or ''}"}

    def detect_quota_limit(self, status: int, body: bytes | str) -> str | None:
        if status in (401, 403):
            return "auth"
        if status in (402, 429):
            return "rate"
        return None


def positive_count(value: str) -> int:
    try:
        count = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Enter a positive whole number") from None
    if count < 1:
        raise argparse.ArgumentTypeError("Enter a positive whole number")
    return count


def prompt_count() -> int:
    while True:
        try:
            return positive_count(input("How many accounts do you want to register? ").strip())
        except argparse.ArgumentTypeError as error:
            print(error, flush=True)


def prompt_mail_service() -> str:
    services = {
        "1": ("tmail", "tmail.wibucrypto.pro (browser)"),
        "2": ("mail.tm", "Mail.tm (API)"),
    }
    while True:
        print("Choose mailbox service:", flush=True)
        for number, (_, label) in services.items():
            print(f"  {number}. {label}", flush=True)
        choice = input("Service [1]: ").strip() or "1"
        if choice in services:
            return services[choice][0]
        print("Enter 1 or 2.", flush=True)


async def register_accounts(provider: EirouterProvider, count: int,
                            log: Callable[[str], None], proxies: list[str] | None = None) -> int:
    if count < 1:
        raise ValueError("Account count must be positive")
    completed = 0
    log(f"[eirouter] Requested: {count}. Mail: {provider.mail_service}. Output: {provider.output}")
    log("[eirouter] Complete Cloudflare in the browser if prompted. Ctrl+C stops the queue.")
    for index in range(1, count + 1):
        log(f"[eirouter] Account {index}/{count}")
        try:
            if proxies:
                proxy = proxies[(index - 1) % len(proxies)]
                account = await provider.register_step(log, proxy=proxy)
            else:
                account = await provider.register_step(log)
        except (RuntimeError, TimeoutError, PlaywrightError, httpx.HTTPError, OSError, ValueError) as error:
            # External errors may contain request data; report the type, not secrets.
            log(f"[eirouter] Stopped on account {index}: {type(error).__name__}. "
                "Check the last step above; saved credentials are retained.")
            break
        if not account.get("confirmed") or not account.get("api_key"):
            log("[eirouter] Stopped: registration or key capture is incomplete. "
                "Use --resume EMAIL for a confirmed account without a key.")
            break
        completed += 1
    log(f"[eirouter] Complete accounts with API keys: {completed}/{count}. File: {provider.output}")
    return 0 if completed == count else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Register eirouter accounts and save their API keys")
    parser.add_argument("--mail-service", choices=("tmail", "mail.tm"), default="tmail")
    parser.add_argument("--output", type=Path, default=EirouterProvider.OUTPUT_PATH)
    parser.add_argument("--profiles-dir", type=Path, help="Browser profiles and mappings (default: next to output)")
    parser.add_argument("--browser", choices=("auto", "chrome", "adspower"), default="auto",
                        help="auto selects AdsPower when adspower.local.json exists")
    parser.add_argument("--adspower-profile", help="Use an existing AdsPower profile (one account per run)")
    parser.add_argument("--adspower-config", type=Path,
                        default=EirouterProvider.OUTPUT_PATH.parent / "adspower.local.json")
    routes = parser.add_mutually_exclusive_group()
    routes.add_argument("--proxy", help="One HTTP CONNECT proxy URL")
    routes.add_argument("--proxies-file", type=Path, help="Proxy list; default: proxies.txt beside this project")
    routes.add_argument("--direct", action="store_true", help="Use the system network route without proxies")
    parser.add_argument("--locale", help="Browser locale; default: exit country or en-US")
    parser.add_argument("--timezone", help="IANA timezone; default: exit IP or system")
    parser.add_argument("--no-geo", action="store_true", help="Disable the ipwho.is location lookup")
    parser.add_argument("--standard-browser", action="store_true", help="Disable compatibility launch flags")
    parser.add_argument("--captcha-timeout", type=positive_count, default=600, help="Cloudflare wait in seconds")
    parser.add_argument("--check-browser", action="store_true", help="Open registration without creating accounts")
    parser.add_argument("--resume", metavar="EMAIL", help="Copy a key for an already saved account")
    quantity = parser.add_mutually_exclusive_group()
    quantity.add_argument("--count", type=positive_count, help="Number of accounts (default: 1)")
    quantity.add_argument("--interactive", action="store_true", help="Ask for account count and mailbox service")
    args = parser.parse_args()
    if args.resume and (args.interactive or args.count is not None):
        parser.error("--resume cannot be combined with --count or --interactive")
    if args.check_browser and (args.resume or args.interactive or args.count is not None):
        parser.error("--check-browser cannot be combined with registration or resume options")
    use_adspower = args.browser == "adspower" or (args.browser == "auto" and args.adspower_config.exists())
    if args.adspower_profile and not use_adspower:
        parser.error("--adspower-profile requires AdsPower")
    config = {}
    if use_adspower and args.adspower_config.exists():
        config = json.loads(args.adspower_config.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            parser.error("AdsPower config must contain a JSON object")
    try:
        proxy_path = args.proxies_file or EirouterProvider.OUTPUT_PATH.parent / "proxies.txt"
        if args.proxies_file and not proxy_path.is_file():
            parser.error("The specified proxy file does not exist")
        if args.direct or (use_adspower and config.get("use_proxy_file") is False
                           and not args.proxy and not args.proxies_file):
            proxies = []
        else:
            proxies = [normalize_proxy(args.proxy)] if args.proxy else load_list(proxy_path)
        if args.adspower_profile:
            if args.proxy or args.proxies_file or args.direct:
                parser.error("Existing AdsPower profiles use their configured proxy")
            proxies = []
    except (ValueError, OSError):
        parser.error("Cannot load proxies: use HTTP CONNECT URLs, one per line")
    if (args.resume or args.check_browser) and len(proxies) > 1:
        parser.error("Choose one --proxy for resume or browser check")
    profile_root = args.profiles_dir or args.output.parent / "profiles"
    if use_adspower:
        api_key = os.environ.get("ADSPOWER_API_KEY") or config.get("api_key")
        if not api_key:
            parser.error("Set ADSPOWER_API_KEY or api_key in adspower.local.json")
        profiles = AdsPowerProfiles(profile_root, config.get("base_url", "http://127.0.0.1:50325"),
                                    api_key, args.adspower_profile)
    else:
        profiles = BrowserProfiles(profile_root, locale=args.locale, timezone=args.timezone,
                                   detect_location=not args.no_geo, compatibility_mode=not args.standard_browser)
    provider = EirouterProvider(args.mail_service, args.output, profiles, args.captcha_timeout)
    if args.output.exists():
        provider.export_keys()
    def log(message: str) -> None:
        print(message, flush=True)
    if args.resume:
        asyncio.run(provider.resume_key(args.resume, log, proxy=proxies[0] if proxies else None))
        return 0
    elif args.check_browser:
        asyncio.run(provider.check_browser(log, proxy=proxies[0] if proxies else None))
        return 0
    else:
        if args.interactive:
            count = prompt_count()
            provider.mail_service = prompt_mail_service()
        else:
            count = args.count or 1
        if args.adspower_profile and count != 1:
            parser.error("An existing AdsPower profile supports one registration per run; omit --adspower-profile for separate new profiles")
        return asyncio.run(register_accounts(provider, count, log, proxies=proxies))


def cli() -> int:
    try:
        return main()
    except (KeyboardInterrupt, EOFError):
        print("\n[eirouter] Stopped. Previously saved credentials are retained.", flush=True)
        return 130
    except (RuntimeError, TimeoutError, PlaywrightError, httpx.HTTPError, OSError, ValueError) as error:
        print(f"[eirouter] Stopped: {type(error).__name__}. Check the last logged step.", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(run_logged(cli, Path(__file__).resolve().parents[1] / "logs"))
