"""Offline tests for mailbox filtering and the website's copy-key contract."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from playwright.async_api import async_playwright

from core.recipes import load_file
from core.recipe_runner import RecipeRunner
from providers.eirouter import EirouterProvider, prompt_count, prompt_mail_service, register_accounts
from providers.eirouter_mail import MailTmClient
from core.adspower import AdsPowerProfiles


def test_prompt_count_rejects_invalid_input(monkeypatch) -> None:
    answers = iter(["", "hello", "0", "-2", "1.5", "3"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert prompt_count() == 3


@pytest.mark.parametrize("inputs,expected", [
    ([""], "tmail"), ([" 1 "], "tmail"), (["3", "invalid", "2"], "mail.tm"),
])
def test_prompt_mail_service(monkeypatch, inputs, expected) -> None:
    answers = iter(inputs)
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert prompt_mail_service() == expected


@pytest.mark.parametrize("service,selection", [("tmail", "1"), ("mail.tm", "2")])
@pytest.mark.parametrize("interactive", [True, False])
def test_cli_passes_mail_selection_to_batch(tmp_path: Path, monkeypatch,
                                           service, selection, interactive) -> None:
    from providers import eirouter

    monkeypatch.setattr(eirouter.EirouterProvider, "OUTPUT_PATH", tmp_path / "accounts.json")
    batch = AsyncMock(return_value=0)
    monkeypatch.setattr(eirouter, "register_accounts", batch)
    prompts = []
    answers = iter(["5", selection])

    def answer(prompt):
        assert interactive, "Non-interactive CLI must not prompt"
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", answer)
    args = ["eirouter", "--browser", "chrome", "--direct"]
    args += ["--interactive"] if interactive else ["--count", "5", "--mail-service", service]
    monkeypatch.setattr("sys.argv", args)
    assert eirouter.main() == 0
    assert batch.await_args.args[0].mail_service == service
    assert batch.await_args.args[1] == 5
    assert prompts == (["How many accounts do you want to register? ", "Service [1]: "]
                       if interactive else [])


def test_key_export_and_credentials_survive_write_failure(tmp_path: Path, monkeypatch) -> None:
    provider = EirouterProvider(output=tmp_path / "eirouter_accounts.json")
    store = provider._records()
    key = "sk-" + "a" * 40
    next_key = "sk-" + "b" * 40
    store.add("one@example.test", "eirouter", password="fixture-password")
    provider._save_key("one@example.test", key)
    provider.export_keys()
    assert provider.keys_path.read_text() == key + "\n"
    store.add("two@example.test", "eirouter", password="another-password")
    replace = Path.replace

    def fail_txt(source, target):
        if target == provider.keys_path:
            raise OSError("disk error")
        return replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail_txt)
        with pytest.raises(OSError):
            provider._save_key("two@example.test", next_key)
    assert provider.keys_path.read_text() == key + "\n"
    records = json.loads(provider.output.read_text())
    assert records[1]["password"] == "another-password"
    assert records[1]["api_key"] == next_key
    provider.export_keys()
    assert provider.keys_path.read_text().splitlines() == [key, next_key]


@pytest.mark.parametrize("saved", [False, True])
def test_profile_deletion_requires_saved_credentials(tmp_path: Path, saved: bool) -> None:
    async def run():
        profiles = AdsPowerProfiles(tmp_path / "profiles", "http://localhost:50325", "fixture")
        profiles.close = AsyncMock()
        profiles.delete_created = AsyncMock()
        provider = EirouterProvider(output=tmp_path / "accounts.json", profiles=profiles)
        context = object()
        await provider._finish_profile(context, "fixture-id", saved, lambda _: None)
        profiles.close.assert_awaited_once_with(context)
        assert profiles.delete_created.await_count == int(saved)
        profiles.close.side_effect = RuntimeError("stop failed")
        profiles.delete_created.reset_mock()
        with pytest.raises(RuntimeError):
            await provider._finish_profile(context, "fixture-id", True, lambda _: None)
        profiles.delete_created.assert_not_awaited()
    asyncio.run(run())


def test_batch_saves_each_account_and_preserves_existing(tmp_path: Path) -> None:
    provider = EirouterProvider(output=tmp_path / "accounts.json")
    store = provider._records()
    store.add("existing@example.test", "eirouter", password="old", api_key="old-key")

    async def register(log):
        number = len(store.all())
        record = store.add(f"user{number}@example.test", "eirouter",
                           password=f"password{number}", api_key=f"key{number}")
        saved = json.loads(provider.output.read_text(encoding="utf-8"))
        assert len(saved) == number + 1
        return {**record, "confirmed": True}

    provider.register_step = AsyncMock(side_effect=register)
    assert asyncio.run(register_accounts(provider, 3, lambda _: None)) == 0
    assert provider.register_step.await_count == 3
    saved = json.loads(provider.output.read_text(encoding="utf-8"))
    assert len(saved) == 4
    assert saved[0]["api_key"] == "old-key"
    assert all(row["email"] and row["password"] and row["api_key"] for row in saved)


@pytest.mark.parametrize("failure", [RuntimeError("SECRET"), {"confirmed": True, "api_key": ""}])
def test_batch_stops_on_incomplete_account(tmp_path: Path, failure) -> None:
    provider = EirouterProvider(output=tmp_path / "accounts.json")
    provider.register_step = AsyncMock(side_effect=[
        {"confirmed": True, "api_key": "fixture-key"}, failure,
    ])
    logs = []
    assert asyncio.run(register_accounts(provider, 3, logs.append)) == 1
    assert provider.register_step.await_count == 2
    assert "1/3" in logs[-1]
    assert "SECRET" not in "\n".join(logs)


def test_mailtm_account_and_code() -> None:
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path == "/domains":
            return httpx.Response(200, json={"hydra:member": [
                {"domain": "private.test", "isActive": True, "isPrivate": True},
                {"domain": "mail.test", "isActive": True, "isPrivate": False},
            ]})
        if path == "/accounts":
            return httpx.Response(201, json={"id": "account"})
        if path == "/token":
            return httpx.Response(200, json={"token": "mail-token"})
        assert request.headers["Authorization"] == "Bearer mail-token"
        if path == "/messages":
            return httpx.Response(200, json={"hydra:member": [{"id": "wrong"}, {"id": "right"}]})
        if path == "/messages/wrong":
            return httpx.Response(200, json={"subject": "Other service code 999999"})
        if path == "/messages/right":
            return httpx.Response(200, json={
                "from": {"address": "verify@eirouter.ai"},
                "subject": "Verify your email", "text": "",
                "html": ['<style>.x{color:#123456}</style><p>Your code: <b>012345</b></p>'],
            })
        raise AssertionError(path)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            mailbox = MailTmClient(client, lambda _: None)
            address = await mailbox.create_mailbox()
            assert address.endswith("@mail.test")
            assert await mailbox.wait_for_code() == "012345"
        assert json.loads(requests[1].content) == json.loads(requests[2].content)
    asyncio.run(run())


def test_mailtm_timeout_and_redacted_error() -> None:
    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"hydra:member": []})
        )) as client:
            mailbox = MailTmClient(client, lambda _: None)
            with pytest.raises(TimeoutError):
                await mailbox.wait_for_code(timeout=0.02, interval=0.001)
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(429, json={"detail": "SECRET"})
        )) as client:
            with pytest.raises(RuntimeError, match="HTTP 429") as error:
                await MailTmClient(client, lambda _: None).create_mailbox()
            assert "SECRET" not in str(error.value)
    asyncio.run(run())


def test_mailtm_selects_domain_from_later_page(monkeypatch) -> None:
    choices = []

    def choose(options):
        choices.append(list(options))
        return options[-1]

    monkeypatch.setattr("providers.eirouter_mail.secrets.choice", choose)

    def handle(request):
        if request.url.path == "/domains":
            if request.url.params.get("page") == "2":
                return httpx.Response(200, json={"hydra:member": [
                    {"domain": "second.test", "isActive": True, "isPrivate": False},
                    {"domain": "private.test", "isActive": True, "isPrivate": True},
                    {"domain": "inactive.test", "isActive": False, "isPrivate": False},
                ]})
            return httpx.Response(200, json={
                "hydra:member": [
                    {"domain": "first.test", "isActive": True, "isPrivate": False},
                ],
                "hydra:view": {"hydra:next": "/domains?page=2"},
            })
        if request.url.path == "/accounts":
            address = json.loads(request.content)["address"]
            assert address.endswith("@second.test")
            return httpx.Response(201, json={"id": "fixture"})
        assert request.url.path == "/token"
        return httpx.Response(200, json={"token": "fixture-token"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            await MailTmClient(client, lambda _: None).create_mailbox()

    asyncio.run(run())
    assert choices[0] == ["first.test", "second.test"]


def test_mailbox_names_vary_words_and_suffix(monkeypatch) -> None:
    monkeypatch.setattr("providers.eirouter_mail.secrets.choice", lambda options: options[0])
    monkeypatch.setattr("providers.eirouter_mail.secrets.token_hex", lambda size: "a" * (size * 2))
    first = MailTmClient.random_username()
    monkeypatch.setattr("providers.eirouter_mail.secrets.choice", lambda options: options[-1])
    monkeypatch.setattr("providers.eirouter_mail.secrets.token_hex", lambda size: "b" * (size * 2))
    second = MailTmClient.random_username()
    assert first != second
    for name in (first, second):
        assert 1 <= len(name) <= 64
        assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789" for c in name)
        assert ".." not in name


def test_registration_rejects_server_error() -> None:
    async def run() -> None:
        response = AsyncMock()
        response.ok = True
        response.json.return_value = {"code": 400, "message": "SECRET"}
        with pytest.raises(RuntimeError, match="rejected") as error:
            await EirouterProvider._check_response(response, "registration")
        assert "SECRET" not in str(error.value)
    asyncio.run(run())


@pytest.mark.parametrize("failed_path", ["/domains", "/accounts", "/token"])
def test_mailtm_logs_failed_stage_without_secrets(failed_path: str) -> None:
    logs = []

    def handle(request):
        if request.url.path == failed_path:
            return httpx.Response(429, json={"detail": "SECRET"})
        if request.url.path == "/domains":
            return httpx.Response(200, json={"hydra:member": [
                {"domain": "example.test", "isActive": True, "isPrivate": False},
            ]})
        return httpx.Response(201, json={"id": "fixture"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with pytest.raises(RuntimeError, match="HTTP 429"):
                await MailTmClient(client, logs.append).create_mailbox()

    asyncio.run(run())
    assert f"{failed_path} failed: HTTP 429" in logs[-1]
    assert "SECRET" not in "\n".join(logs)
    assert "Mailbox created" not in "\n".join(logs)


@pytest.mark.parametrize("failure", ["network", "json", "token"])
def test_mailtm_logs_invalid_responses_and_network_errors(failure: str) -> None:
    logs = []

    def handle(request):
        if failure == "network":
            raise httpx.ConnectError("SECRET", request=request)
        if failure == "json":
            return httpx.Response(200, text="SECRET")
        return httpx.Response(200, json={"detail": "SECRET"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with pytest.raises((httpx.ConnectError, RuntimeError)):
                await MailTmClient(client, logs.append).login("fixture@example.test", "SECRET")

    asyncio.run(run())
    expected = {"network": "ConnectError", "json": "invalid JSON", "token": "no access token"}
    assert expected[failure] in logs[-1]
    assert "SECRET" not in "\n".join(logs)


def test_form_and_key_capture_in_browser(tmp_path: Path) -> None:
    async def run() -> None:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, channel="chrome")
            try:
                page = await browser.new_page()
                await page.set_content('<input id="reg-code"><input id="reg-password" type="password">'
                                       '<input id="reg-terms" type="checkbox">')
                recipe = load_file(EirouterProvider.RECIPE_PATH)
                await RecipeRunner(page, context_vars={"code": "012345", "password": "Test-secret"},
                                   log=lambda _: None).run(recipe)
                assert await page.locator("#reg-code").input_value() == "012345"
                assert await page.locator("#reg-password").input_value() == "Test-secret"
                assert await page.locator("#reg-terms").is_checked()
                await page.set_content('<div class="auth-turnstile"></div>')
                provider = EirouterProvider(output=tmp_path / "records.json")
                logs = []
                waiting = asyncio.create_task(provider._wait_cloudflare(page, logs.append))
                await asyncio.sleep(0.1)
                assert not waiting.done()
                await page.evaluate("""() => {
                    const input = document.createElement('input');
                    input.name = 'cf-turnstile-response';
                    input.value = 'fixture-token';
                    document.querySelector('.auth-turnstile').appendChild(input);
                }""")
                await waiting
                assert len(logs) == 1
                key = "sk-" + "a" * 40
                html = ('<div class="app-modal__footer" style="position:fixed;inset:0;z-index:10;background:white">'
                        '<button onclick="this.parentElement.remove()">Got it</button></div>'
                        '<button aria-controls="panel-keys" onclick="document.getElementById(\'panel-keys\').hidden=false">Keys</button>'
                        '<div id="panel-keys" hidden>'
                        '<section class="account-key-card"><button class="akc-key-btn" '
                        f'onclick="navigator.clipboard.writeText(\'{key}\')">sk-aaa****aaaa</button>'
                        '</section></div>')
                await page.route(EirouterProvider.CONSOLE_URL,
                                 lambda route: route.fulfill(content_type="text/html", body=html))
                assert await provider._copy_key(page) == key
                html = html.replace(key, "sk-aaa****aaaa")
                with pytest.raises(RuntimeError, match="complete API key"):
                    await provider._copy_key(page)
                assert await page.evaluate("() => !('__eirouterCopiedKey' in window)")
            finally:
                await browser.close()
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["no_news", "hidden_key", "visible_key", "during_key_click"])
def test_key_capture_dismisses_announcement_queue(tmp_path: Path, mode: str) -> None:
    async def run() -> None:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, channel="chrome")
            try:
                page = await browser.new_page()
                key = "sk-" + "b" * 40
                html = """
                    <meta charset="utf-8">
                    <div class="app-modal__footer" hidden><button>Got it</button></div>
                    <button aria-controls="panel-keys"
                        onclick="document.getElementById('panel-keys').hidden=false">Keys</button>
                    <div id="panel-keys"><section class="account-key-card">
                        <button class="akc-key-btn" onclick="copyKey()">Copy key</button>
                    </section></div>
                    <div id="notice" class="app-modal__footer"
                        style="position:fixed;inset:0;z-index:10;background:white">
                        <p id="news">News 1</p><button onclick="dismiss()">Got it</button>
                    </div>
                    <script>
                        const mode = MODE;
                        const notice = document.getElementById('notice');
                        window.dismissed = 0;
                        window.copyClicks = 0;
                        document.getElementById('panel-keys').hidden = mode === 'hidden_key';
                        if (mode === 'no_news') notice.remove();
                        function dismiss() {
                            window.dismissed++;
                            if (window.dismissed === 3) notice.remove();
                            else {
                                document.getElementById('news').textContent = 'News ' + (window.dismissed + 1);
                                notice.querySelector('button').textContent = window.dismissed === 1
                                    ? '\u6211\u77e5\u9053\u4e86' : ' I understand ';
                            }
                        }
                        function copyKey() {
                            window.copyClicks++;
                            navigator.clipboard.writeText(KEY);
                        }
                        if (mode === 'during_key_click') {
                            notice.hidden = true;
                            // Show the overlay after Playwright checks the key's visibility.
                            document.querySelector('.akc-key-btn').addEventListener('mousemove', () => {
                                notice.hidden = false;
                            }, {once: true});
                        }
                    </script>
                """.replace("MODE", json.dumps(mode)).replace("KEY", json.dumps(key))
                await page.route(EirouterProvider.CONSOLE_URL,
                                 lambda route: route.fulfill(content_type="text/html", body=html))
                provider = EirouterProvider(output=tmp_path / "records.json")
                assert await provider._copy_key(page) == key
                assert await page.evaluate("() => window.dismissed") == (0 if mode == "no_news" else 3)
                assert await page.evaluate("() => window.copyClicks") == 1
                assert await page.evaluate("() => !('__eirouterCopiedKey' in window)")
                assert await page.evaluate("() => !('__eirouterOriginalCopy' in window)")
            finally:
                await browser.close()
    asyncio.run(run())
