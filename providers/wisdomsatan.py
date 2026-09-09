"""Register accounts on wisdomsatan.club."""

import argparse
import asyncio
import json
import secrets
import string
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from playwright.async_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout, async_playwright

from core.adspower import AdsPowerProfiles
from core.browser_profile import BrowserProfiles
from core.navigation import goto_with_retry
from core.run_logs import run_logged
from core.storage import AccountStore


REGISTER_URL = "https://api.wisdomsatan.club/register?aff=kiW4"
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "wisdomsatan_accounts.json"


def username() -> str:
    words = ("amber", "brisk", "cosmic", "lucky", "rapid", "silver", "smart", "sunny", "urban")
    nouns = ("atlas", "comet", "maple", "orbit", "pixel", "river", "signal", "spark", "vertex")
    return f"{secrets.choice(words)}_{secrets.choice(nouns)}{secrets.token_hex(3)}"


def password() -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    # WisdomSatan validates User.Password with a relatively small max length.
    return "Ws9!" + "".join(secrets.choice(alphabet) for _ in range(12))


def prompt_count() -> int:
    while True:
        try:
            value = int(input("How many accounts do you want to register? ").strip())
            if value > 0:
                return value
        except ValueError:
            pass
        print("Enter a positive whole number.", flush=True)


async def register_one(profiles, output: Path, log) -> dict[str, object]:
    login, secret = username(), password()
    profile_id = str(uuid.uuid4())
    store = AccountStore(output)
    context = None
    async with async_playwright() as pw:
        try:
            context = await profiles.launch(pw, profile_id, None, log)
            store.add(login, "wisdomsatan", password=secret, status="registration_pending",
                      device_uuid=profile_id)
            page = await context.new_page()
            await goto_with_retry(page, REGISTER_URL, wait_until="domcontentloaded", attempts=3, log=log)
            await page.get_by_role("textbox", name="Имя пользователя").fill(login)
            await page.get_by_role("textbox", name="Пароль", exact=True).fill(secret)
            await page.get_by_role("textbox", name="Подтвердить пароль").fill(secret)
            consent = page.get_by_role("checkbox").first
            if not await consent.is_checked():
                # Semi UI renders the visual toggle beside a hidden input.
                display = page.locator(".semi-checkbox-inner-display").first
                await display.click(force=True)
                if not await consent.is_checked():
                    await consent.evaluate(
                        """input => {
                            input.click();
                            input.dispatchEvent(new Event('change', {bubbles: true}));
                        }"""
                    )
                await page.wait_for_function(
                    """() => {
                        const input = document.querySelector('input[type="checkbox"]');
                        return !!input?.checked;
                    }""",
                    timeout=5_000,
                )
            # The header has another button with the same label.
            button = page.locator('button[type="submit"]').filter(has_text="Регистрация").first
            if not await button.count():
                button = page.get_by_role("main").get_by_role(
                    "button", name="Регистрация", exact=True).first
            await page.wait_for_function(
                """() => {
                    const input = document.querySelector('input[type="checkbox"]');
                    const buttons = [...document.querySelectorAll('button')];
                    const button = buttons.filter(item =>
                        item.textContent.includes('注册') ||
                        item.textContent.includes('Регистрация')).at(-1);
                    return input?.checked && button && !button.disabled;
                }""",
                timeout=10_000,
            )
            await button.click()
            try:
                await page.wait_for_url(lambda url: urlsplit(str(url)).path != "/register", timeout=20_000)
            except PlaywrightTimeout:
                raise RuntimeError("Registration was not confirmed; credentials saved as pending") from None
            store.set_status(login, "active")
            store.save()
            log(f"[wisdomsatan] Registered {login}; credentials saved to {output}")
            return store.get(login) or {"email": login, "password": secret}
        finally:
            failed = sys.exception() is not None
            try:
                if context is not None:
                    await profiles.close(context)
                if isinstance(profiles, AdsPowerProfiles):
                    if context is not None:
                        await profiles.delete_created(profile_id, log)
                    else:
                        await profiles.cleanup_owned_profiles(log)
            except (RuntimeError, httpx.HTTPError, OSError, ValueError):
                log("[wisdomsatan] Profile cleanup failed; it will be retried before the next run")
                if not failed:
                    raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Register wisdomsatan.club accounts")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--interactive", action="store_true", help="Ask how many accounts to register")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--browser", choices=("chrome", "adspower"), default="adspower")
    parser.add_argument("--adspower-config", type=Path, default=ROOT / "adspower.local.json")
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count must be positive")
    count = prompt_count() if args.interactive else args.count
    root = args.output.parent / "profiles"
    if args.browser == "adspower":
        config = json.loads(args.adspower_config.read_text(encoding="utf-8"))
        key = config.get("api_key")
        if not key:
            parser.error("AdsPower config has no api_key")
        profiles = AdsPowerProfiles(root, config.get("base_url", "http://127.0.0.1:50325"), key)
    else:
        profiles = BrowserProfiles(root)
    def log(message: str) -> None:
        print(message, flush=True)
    failures = 0
    for index in range(count):
        log(f"[wisdomsatan] Account {index + 1}/{count}")
        try:
            asyncio.run(register_one(profiles, args.output, log))
        except (RuntimeError, TimeoutError, PlaywrightError, httpx.HTTPError, OSError, ValueError) as error:
            failures += 1
            log(f"[wisdomsatan] Account {index + 1} failed: {type(error).__name__}: {error}")
            log(f"[wisdomsatan] Continuing with the next account ({failures} failed so far)")
    if failures:
        log(f"[wisdomsatan] Finished with {failures}/{count} failed account(s); "
            f"see logs/ for details")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run_logged(main, ROOT / "logs"))
