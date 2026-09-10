import asyncio
import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from playwright.async_api import async_playwright

from core.browser_profile import BrowserProfiles
from core.proxy_pool import load_list, playwright_proxy
from providers.eirouter import EirouterProvider, register_accounts


def test_profile_settings_survive_resume(tmp_path: Path) -> None:
    async def run() -> None:
        profiles = BrowserProfiles(tmp_path, locale="en-GB", timezone="Europe/London", detect_location=False)
        profile_id = str(uuid.uuid4())
        proxy = "http://user:SECRET@localhost:3128"
        folder, settings = await profiles.settings(profile_id, proxy, lambda _: None)
        again = BrowserProfiles(tmp_path, locale="de-DE", detect_location=False)
        assert await again.settings(profile_id, proxy, lambda _: None) == (folder, settings)
        assert settings["timezone_id"] == "Europe/London"
        assert settings["locale"] == "en-GB"
        assert "SECRET" not in (folder / "browser-settings.json").read_text()
        with pytest.raises(ValueError, match="original proxy"):
            await again.settings(profile_id, None, lambda _: None)
        other, _ = await again.settings(str(uuid.uuid4()), None, lambda _: None)
        assert other != folder
    asyncio.run(run())


def test_geo_lookup_uses_selected_proxy(tmp_path: Path, monkeypatch) -> None:
    calls = []
    original = httpx.AsyncClient

    def client(**kwargs):
        calls.append(kwargs)
        return original(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={
            "success": True, "country_code": "DE", "timezone": {"id": "Europe/Berlin"},
        })))

    monkeypatch.setattr("core.browser_profile.httpx.AsyncClient", client)

    async def run() -> None:
        profiles = BrowserProfiles(tmp_path)
        _, settings = await profiles.settings(str(uuid.uuid4()), "http://localhost:3128", lambda _: None)
        assert settings["locale"] == "de-DE"
        assert settings["timezone_id"] == "Europe/Berlin"
        assert calls == [{"proxy": "http://localhost:3128", "timeout": 8, "trust_env": False}]
    asyncio.run(run())


def test_proxy_list_validation(tmp_path: Path) -> None:
    file = tmp_path / "proxies.txt"
    file.write_text("# Comment\nhost.test:3128\nhttp://host.test:3128\n", encoding="utf-8")
    assert load_list(file) == ["http://host.test:3128"]
    assert playwright_proxy("http://user:p%40ss@[::1]:3128") == {
        "server": "http://[::1]:3128", "username": "user", "password": "p@ss",
    }
    file.write_text("http://user:SECRET@host.test:invalid", encoding="utf-8")
    with pytest.raises(ValueError) as error:
        load_list(file)
    assert "SECRET" not in str(error.value)


def test_batch_selects_proxy_per_account(tmp_path: Path) -> None:
    provider = EirouterProvider(output=tmp_path / "accounts.json")
    provider.register_step = AsyncMock(return_value={"confirmed": True, "api_key": "fixture"})
    proxies = ["http://first.test:3128", "http://second.test:3128"]
    assert asyncio.run(register_accounts(provider, 3, lambda _: None, proxies=proxies)) == 0
    assert [call.kwargs["proxy"] for call in provider.register_step.await_args_list] == [
        proxies[0], proxies[1], proxies[0],
    ]


def test_chrome_profile_persists_session_and_settings(tmp_path: Path) -> None:
    async def run() -> None:
        profiles = BrowserProfiles(tmp_path, locale="en-GB", timezone="Europe/London", detect_location=False)
        profile_id = str(uuid.uuid4())
        async with async_playwright() as pw:
            for iteration in range(2):
                context = await profiles.launch(pw, profile_id, None, lambda _: None, headless=True)
                try:
                    page = await context.new_page()
                    await page.route("https://profile.test/", lambda route: route.fulfill(body="Profile test"))
                    await page.goto("https://profile.test/")
                    actual = await page.evaluate("""() => ({
                        locale: navigator.language,
                        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                        width: window.innerWidth,
                        screenWidth: screen.width,
                    })""")
                    assert actual["locale"] == "en-GB"
                    assert actual["timezone"] == "Europe/London"
                    assert actual["width"] == actual["screenWidth"]
                    if iteration == 0:
                        await page.evaluate("() => localStorage.setItem('session-test', 'retained')")
                    else:
                        assert await page.evaluate("() => localStorage.getItem('session-test')") == "retained"
                finally:
                    await context.close()
    asyncio.run(run())
