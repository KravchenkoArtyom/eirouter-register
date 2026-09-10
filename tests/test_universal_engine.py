"""Движок сценариев без браузера: подстановки, почтовые шаги, паузы, валидация."""
import asyncio
import time

import pytest

from universal import mail as mail_module
from universal import scenarios as scenario_store
from universal.actions import (ACTIONS, GUARD_ACTIONS, MAIL_ACTIONS, PAGE_ACTIONS,
                               needs_selector, required_fields)
from universal.control import RunControl
from universal.pacing import Pacing
from universal.runner import ScenarioRunner, format_value, scenario_service


class FakeMailbox:
    """Ящик-заглушка: считает вызовы и отдаёт заранее известные значения."""

    def __init__(self, address="user@example.test"):
        self.address = address
        self.link_keywords = ("verify",)
        self.calls = []

    async def wait_for_code(self, length=6, timeout=180, sender_hint=None):
        self.calls.append(("code", length, timeout, sender_hint))
        return "1" * length

    async def wait_for_link(self, timeout=300):
        self.calls.append(("link", timeout))
        return "https://example.test/verify?token=abc"

    async def refresh(self):
        self.calls.append(("refresh",))


class FakePage:
    """Страница-заглушка: запоминает переходы, созданные из mail_open_link."""

    def __init__(self):
        self.visited = []
        self.context = self

    async def goto(self, url, wait_until=None, **kwargs):
        self.visited.append(url)

    async def wait_for_timeout(self, milliseconds):
        return None

    async def new_page(self):
        return self

    async def close(self):
        self.visited.append("closed")


def build_runner(service="tmail"):
    page = FakePage()
    variables = {"email": "", "login": "leon", "password": "secret"}
    hub = mail_module.MailHub(None, None, service, lambda message: None, variables)
    hub.mailbox = FakeMailbox()
    hub.service = service
    hub._publish()
    return page, variables, hub, ScenarioRunner(page, variables, lambda message: None, hub)


# ---- подстановки ----
def test_format_value_keeps_unknown_placeholder():
    assert format_value("{email} {mystery}", {"email": "a@b.test"}) == "a@b.test {mystery}"


def test_scenario_service_prefers_argument_then_scenario():
    assert scenario_service({"mail": "mail.tm"}, "tmail") == "tmail"
    assert scenario_service({"mail": "wibucrypto"}, "") == "tmail"
    assert scenario_service({}, "") == "none"


# ---- каталог действий ----
def test_catalog_is_consistent():
    assert needs_selector("fill") and not needs_selector("mail_create")
    assert required_fields("press") == ("key",)
    for name in MAIL_ACTIONS:
        assert ACTIONS[name]["group"] == "mail"
        assert ACTIONS[name]["selector"] == "none"
    for name in GUARD_ACTIONS:
        assert ACTIONS[name]["group"] == "guard"
    # шаги почтового сервиса — это страничные действия без почтовых
    assert set(PAGE_ACTIONS).isdisjoint(MAIL_ACTIONS)
    assert set(GUARD_ACTIONS) <= set(PAGE_ACTIONS)


# ---- почтовые шаги ----
def test_mail_wait_code_fills_variable():
    _, variables, hub, runner = build_runner()
    asyncio.run(runner.run([{"action": "mail_wait_code", "length": 4, "timeout": 5}]))
    assert variables["code"] == "1111"
    assert hub.mailbox.calls[0][:3] == ("code", 4, 5)


def test_mail_wait_code_supports_custom_variable_and_legacy_name():
    _, variables, _, runner = build_runner()
    asyncio.run(runner.run([{"action": "mail_wait_code", "var": "otp"},
                            {"action": "wait_email_code"}]))
    assert variables["otp"] == "111111" and variables["code"] == "111111"


def test_mail_wait_link_and_open_link_in_same_tab():
    page, variables, _, runner = build_runner()
    asyncio.run(runner.run([{"action": "mail_wait_link", "timeout": 7},
                            {"action": "mail_open_link"}]))
    assert variables["link"].endswith("token=abc")
    assert page.visited == ["https://example.test/verify?token=abc"]


def test_mail_open_link_waits_when_variable_is_empty():
    page, variables, hub, runner = build_runner()
    asyncio.run(runner.run([{"action": "mail_open_link", "target": "new_tab",
                             "close_after": True, "keywords": "confirm"}]))
    assert ("link", 300) in hub.mailbox.calls
    assert page.visited == ["https://example.test/verify?token=abc", "closed"]
    assert hub.mailbox.link_keywords == ("confirm",)


def test_mail_refresh_calls_client():
    _, _, hub, runner = build_runner()
    asyncio.run(runner.run([{"action": "mail_refresh"}]))
    assert ("refresh",) in hub.mailbox.calls


def test_mail_step_without_mailbox_explains_itself():
    runner = ScenarioRunner(FakePage(), {}, lambda message: None, None)
    with pytest.raises(RuntimeError, match="почтовые действия"):
        asyncio.run(runner.run([{"action": "mail_wait_code"}]))
    hub = mail_module.MailHub(None, None, "none", lambda message: None, {})
    runner = ScenarioRunner(FakePage(), {}, lambda message: None, hub)
    with pytest.raises(RuntimeError, match="Ящик не создан"):
        asyncio.run(runner.run([{"action": "mail_wait_code"}]))


def test_mail_create_publishes_address(monkeypatch):
    variables = {}
    hub = mail_module.MailHub(None, None, "tmail", lambda message: None, variables)

    async def fake_create(service, context, client, log, username=None):
        assert service == "mail.tm"
        return FakeMailbox("fresh@example.test")

    monkeypatch.setattr(mail_module, "create_mailbox", fake_create)
    runner = ScenarioRunner(FakePage(), variables, lambda message: None, hub)
    asyncio.run(runner.run([{"action": "mail_create", "service": "mail.tm"}]))
    assert variables["email"] == "fresh@example.test"
    assert hub.service == "mail.tm"


def test_mail_create_without_service_is_rejected():
    hub = mail_module.MailHub(None, None, "none", lambda message: None, {})
    with pytest.raises(RuntimeError, match="не выбран почтовый сервис"):
        asyncio.run(hub.create())


def test_keywords_accepts_string_list_and_default():
    assert mail_module.keywords_of("verify, Confirm") == ("verify", "confirm")
    assert mail_module.keywords_of(["Activate"]) == ("activate",)
    assert mail_module.keywords_of(None) == mail_module.DEFAULT_LINK_KEYWORDS


# ---- страничные шаги ----
def test_unknown_action_is_rejected():
    _, _, _, runner = build_runner()
    with pytest.raises(ValueError, match="Неподдерживаемое действие"):
        asyncio.run(runner.run([{"action": "teleport"}]))


def test_wait_step_does_not_need_selector():
    _, _, _, runner = build_runner()
    asyncio.run(runner.run([{"action": "wait", "seconds": 0}]))


# ---- капча, паузы, уведомления ----
def test_notify_step_reaches_control():
    seen = []
    control = RunControl(lambda message: None, seen.append)
    runner = ScenarioRunner(FakePage(), {"login": "leon"}, lambda message: None,
                            None, None, None, control)
    asyncio.run(runner.run([{"action": "notify", "message": "проверьте {login}",
                             "level": "warn"}]))
    assert seen[-1]["message"] == "проверьте leon" and seen[-1]["level"] == "warn"


def test_wait_random_stays_in_bounds():
    messages = []
    runner = ScenarioRunner(FakePage(), {}, messages.append)
    started = time.monotonic()
    asyncio.run(runner.run([{"action": "wait_random", "min_seconds": 0, "max_seconds": 0.2}]))
    spent = time.monotonic() - started
    assert spent <= 1
    assert any("пауза" in message for message in messages)


def test_pacing_is_applied_between_steps():
    pacing = Pacing(step_min_ms=40, step_max_ms=60, click_extra_ms=0)
    runner = ScenarioRunner(FakePage(), {}, lambda message: None, None, None, pacing)
    started = time.monotonic()
    asyncio.run(runner.run([{"action": "wait", "seconds": 0},
                            {"action": "wait", "seconds": 0}]))
    assert time.monotonic() - started >= 0.07


def test_stop_request_interrupts_run():
    control = RunControl(lambda message: None)
    control.request_stop()
    runner = ScenarioRunner(FakePage(), {}, lambda message: None, None, None, None, control)
    with pytest.raises(RuntimeError, match="остановлен"):
        asyncio.run(runner.run([{"action": "wait", "seconds": 0}]))


def test_captcha_wait_without_guard_uses_own_watch(monkeypatch):
    """Шаг captcha_wait работает и без сторожа запуска: создаёт его сам."""
    from universal import captcha as captcha_module

    async def no_captcha(page, settings=None):
        return None

    monkeypatch.setattr(captcha_module, "detect", no_captcha)
    runner = ScenarioRunner(FakePage(), {}, lambda message: None)
    asyncio.run(runner.run([{"action": "captcha_wait", "timeout": 1}]))


# ---- сценарии ----
def test_validate_accepts_shipped_scenarios():
    for scenario in scenario_store.load_scenarios(include_hidden=True):
        assert scenario_store.validate(scenario) == [], scenario["_path"].name


def test_validate_checks_url_scheme_and_dom_operation():
    assert scenario_store.validate({"url": "site.test"})
    assert scenario_store.validate({"url": "https://site.test",
                                    "steps": [{"action": "dom", "selectors": ["#x"],
                                               "operation": "explode"}]})


def test_templates_and_slugify():
    assert "email_code" in scenario_store.TEMPLATES
    assert scenario_store.slugify("Мой Сайт #1") == "мой-сайт-1"
    assert scenario_store.slugify("  ") == ""


def test_iter_sections_and_mail_steps():
    scenario = {"url": "https://site.test",
                "steps": [{"action": "mail_create"}, {"action": "click", "selectors": ["#a"]}],
                "verification": {"wait": [{"action": "mail_wait_code"}]}}
    assert [path for path, _ in scenario_store.iter_sections(scenario)] == ["steps", "verification.wait"]
    assert len(scenario_store.mail_steps(scenario)) == 2
    assert scenario_store.has_action(scenario, "mail_create")
    assert not scenario_store.has_action(scenario, "mail_refresh")


def test_validate_checks_delays_and_captcha_blocks():
    assert scenario_store.validate({"url": "https://site.test",
                                    "delays": {"step_min_ms": 100},
                                    "captcha": {"mode": "pause"}}) == []
    assert scenario_store.validate({"url": "https://site.test", "delays": []})
    assert scenario_store.validate({"url": "https://site.test",
                                    "captcha": {"mode": "молиться"}})


def test_validate_knows_custom_mail_service(monkeypatch):
    monkeypatch.setattr("universal.scenarios.known_ids", lambda: ("none", "tmail", "my-mail"))
    payload = {"url": "https://site.test",
               "steps": [{"action": "mail_create", "service": "my-mail"}]}
    assert scenario_store.validate(payload) == []
    payload["steps"][0]["service"] = "чужая-почта"
    assert scenario_store.validate(payload)
