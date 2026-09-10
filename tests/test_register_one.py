"""Полный проход одного аккаунта без браузера: файл аккаунтов и снимок падения.

Плейрайт подменяется заглушкой: нам важно не то, как кликается кнопка, а что
происходит вокруг — какая запись остаётся в `universal_accounts.json`, что
попадает в `logs/failures/` и закрывается ли профиль браузера.
"""
import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from core.storage import AccountStore
from universal import runner as runner_module
from universal.runner import register_one

SCENARIO = {"name": "demo", "url": "https://example.test/signup", "mail": "none",
            "steps": [{"action": "wait", "seconds": 0}]}


class FakeFrame:
    """Кадр без капчи: страж спрашивает его и получает «ничего не нашёл»."""

    def __init__(self, page) -> None:
        self.page = page

    @property
    def url(self) -> str:
        return self.page.url

    async def evaluate(self, script, argument=None):
        return None


class FakePage:
    """Страница-заглушка: помнит переходы и умеет «упасть» на шаге."""

    def __init__(self, fail: str = "") -> None:
        self.visited: list[str] = []
        self.fail = fail
        self.screenshots: list[str] = []
        self.main_frame = FakeFrame(self)
        self.frames = [self.main_frame]

    @property
    def url(self) -> str:
        return self.visited[-1] if self.visited else ""

    async def goto(self, url, wait_until=None, **kwargs):
        self.visited.append(url)

    async def wait_for_timeout(self, milliseconds):
        return None

    async def wait_for_url(self, url, timeout=None):
        if self.fail == "step":
            raise RuntimeError("страница не дождалась адреса")

    async def screenshot(self, path=None, full_page=False):
        self.screenshots.append(str(path))
        Path(path).write_bytes(b"PNG")

    async def content(self):
        return "<html><body>упавшая страница</body></html>"


class FakeContext:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.closed = False

    async def new_page(self):
        return self.page


class FakeProfiles:
    """Профили браузера: считает запуски и закрытия."""

    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.launched: list[tuple[str, str | None]] = []
        self.closed = 0

    async def launch(self, pw, account_id, proxy, log):
        self.launched.append((account_id, proxy))
        log("[fake] профиль запущен")
        return FakeContext(self.page)

    async def close(self, context):
        self.closed += 1


@pytest.fixture
def fake_playwright(monkeypatch):
    @asynccontextmanager
    async def factory():
        yield object()

    monkeypatch.setattr(runner_module, "async_playwright", factory)


def run_one(scenario, profiles, output, **kwargs):
    logs: list[str] = []
    account = asyncio.run(register_one(scenario, "none", profiles, output, None,
                                       logs.append, **kwargs))
    return account, logs


def test_register_one_saves_active_account(tmp_path, fake_playwright):
    page = FakePage()
    profiles = FakeProfiles(page)
    output = tmp_path / "universal_accounts.json"
    account, logs = run_one(SCENARIO, profiles, output)
    assert account["status"] == "active"
    assert account["provider"] == "demo"
    assert len(account["password"]) >= 20
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert [item["email"] for item in saved] == [account["email"]]
    assert page.visited == ["https://example.test/signup"]
    assert profiles.launched[0][1] is None and profiles.closed == 1
    assert any("сохранён" in line for line in logs)


def test_register_one_reuses_shared_store(tmp_path, fake_playwright):
    """Хранилище на весь запуск: файл пишется один раз, записи не теряются."""
    output = tmp_path / "accounts.json"
    store = AccountStore(output)
    try:
        first, _ = run_one(SCENARIO, FakeProfiles(FakePage()), output, store=store)
        second, _ = run_one(SCENARIO, FakeProfiles(FakePage()), output, store=store)
    finally:
        store.close()
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert sorted(item["email"] for item in saved) == sorted([first["email"], second["email"]])
    assert all(item["status"] == "active" for item in saved)


def test_register_one_keeps_failure_artifacts(tmp_path, fake_playwright):
    """Упал шаг — остаются снимок, HTML и описание ошибки."""
    page = FakePage(fail="step")
    profiles = FakeProfiles(page)
    output = tmp_path / "accounts.json"
    artifacts = tmp_path / "failures"
    scenario = dict(SCENARIO, steps=[{"action": "wait_url",
                                      "url": "https://example.test/done"}])
    with pytest.raises(runner_module.StepFailed, match="страница не дождалась"):
        run_one(scenario, profiles, output, artifacts=artifacts)
    folders = list(artifacts.iterdir())
    assert len(folders) == 1
    assert (folders[0] / "screen.png").exists()
    assert "упавшая страница" in (folders[0] / "page.html").read_text(encoding="utf-8")
    error = json.loads((folders[0] / "error.json").read_text(encoding="utf-8"))
    assert error["url"] == "https://example.test/signup"
    assert "StepFailed" in error["error"]
    # запись аккаунта осталась незавершённой, а профиль всё равно закрыт
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved[0]["status"] == "registration_pending"
    assert profiles.closed == 1


def test_register_one_checks_scenario_before_browser(tmp_path, fake_playwright):
    profiles = FakeProfiles(FakePage())
    with pytest.raises(ValueError, match="Сценарий не пройдёт"):
        run_one({"name": "плохой", "steps": []}, profiles, tmp_path / "a.json")
    assert profiles.launched == []
