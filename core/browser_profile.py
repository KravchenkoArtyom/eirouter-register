"""Persistent, isolated Chrome settings for registration and later sign-in."""

import hashlib
import json
import secrets
import uuid
from collections.abc import Callable
from pathlib import Path

import httpx
from playwright.async_api import BrowserContext, Playwright

from core.proxy_pool import playwright_proxy, redact_proxy

VIEWPORTS = ((1366, 768), (1440, 900), (1536, 864), (1600, 900), (1920, 1080))
LOCALES = {"US": "en-US", "GB": "en-GB", "CA": "en-CA", "DE": "de-DE",
           "FR": "fr-FR", "NL": "nl-NL", "RU": "ru-RU", "SG": "en-SG"}


class BrowserProfiles:
    def __init__(self, root: Path, *, locale: str | None = None,
                 timezone: str | None = None, detect_location: bool = True,
                 compatibility_mode: bool = True) -> None:
        self.root = root
        self.locale = locale
        self.timezone = timezone
        self.detect_location = detect_location
        self.compatibility_mode = compatibility_mode

    async def settings(self, profile_id: str, proxy: str | None,
                       log: Callable[[str], None]) -> tuple[Path, dict[str, object]]:
        folder = self.root / uuid.UUID(profile_id).hex
        folder.mkdir(parents=True, exist_ok=True)
        config = folder / "browser-settings.json"
        route_id = hashlib.sha256((proxy or "direct").encode()).hexdigest()
        if config.exists():
            data = json.loads(config.read_text(encoding="utf-8"))
            if data["route_id"] != route_id:
                raise ValueError("Resume this profile with its original proxy")
            return folder, data["options"]

        timezone, locale = self.timezone, self.locale
        if self.detect_location and (not timezone or not locale):
            try:
                # Geo lookup uses the browser's route; a failed proxy never falls back to direct.
                async with httpx.AsyncClient(proxy=proxy, timeout=8, trust_env=False) as client:
                    response = await client.get("https://ipwho.is/")
                    response.raise_for_status()
                    geo = response.json()
                if geo.get("success") is not False:
                    timezone = timezone or (geo.get("timezone") or {}).get("id")
                    locale = locale or LOCALES.get(geo.get("country_code"))
            except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                log("[browser] Location lookup unavailable; using local timezone unless configured")
        width, height = secrets.choice(VIEWPORTS)
        options = {"locale": locale or "en-US", "viewport": {"width": width, "height": height},
                   "screen": {"width": width, "height": height}, "device_scale_factor": 1}
        if timezone:
            options["timezone_id"] = timezone
        config.write_text(json.dumps({"route_id": route_id, "options": options}, indent=2), encoding="utf-8")
        return folder, options

    async def launch(self, pw: Playwright, profile_id: str, proxy: str | None,
                     log: Callable[[str], None], *, headless: bool = False) -> BrowserContext:
        folder, options = await self.settings(profile_id, proxy, log)
        route = redact_proxy(proxy) if proxy else "direct"
        log(f"[browser] Profile: {folder.name}; route: {route}; locale: {options['locale']}; "
            f"timezone: {options.get('timezone_id', 'system')}; viewport: {options['viewport']}")
        # Native Chrome supplies UA, Client Hints, graphics and platform consistently.
        launch_args = ["--no-first-run", "--no-default-browser-check"]
        ignored_args = []
        if self.compatibility_mode:
            launch_args.append("--disable-blink-features=AutomationControlled")
            ignored_args.append("--enable-automation")
        if proxy:
            launch_args.append("--force-webrtc-ip-handling-policy=disable_non_proxied_udp")
        context = await pw.chromium.launch_persistent_context(
            str(folder / "chrome"), channel="chrome", headless=headless,
            proxy=playwright_proxy(proxy) if proxy else None,
            args=launch_args, ignore_default_args=ignored_args, **options)
        context.set_default_timeout(30_000)
        context.set_default_navigation_timeout(120_000)
        return context

    async def close(self, context: BrowserContext) -> None:
        await context.close()
