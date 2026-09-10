"""Обнаружение капчи и поведение сторожа: ждать, пауза, пропуск, лог.

Страницу поднимаем настоящим Chromium (async-API Playwright), потому что
детектор смотрит на видимость элементов глазами браузера.
"""
import asyncio

import pytest

from universal.captcha import (CaptchaGuard, CaptchaSettings, CaptchaSkipped, CaptchaStop,
                               detect)
from universal.control import RunControl

CLEAN = "<html><body><h1>Форма</h1><input name=email></body></html>"
RECAPTCHA = ("<html><body><div class='g-recaptcha' "
             "style='width:300px;height:80px'>проверка</div></body></html>")
HCAPTCHA = ("<html><body><iframe src='https://hcaptcha.com/widget' "
            "width=300 height=80></iframe></body></html>")
HIDDEN = "<html><body><div class='g-recaptcha' style='display:none'>x</div></body></html>"
TEXT_ONLY = "<html><body><p>Подтвердите, что вы человек</p></body></html>"


@pytest.fixture(scope="module")
def loop():
    instance = asyncio.new_event_loop()
    yield instance
    instance.close()


@pytest.fixture(scope="module")
def browser(loop):
    from playwright.async_api import async_playwright
    playwright = loop.run_until_complete(async_playwright().start())
    instance = loop.run_until_complete(playwright.chromium.launch())
    yield instance
    loop.run_until_complete(instance.close())
    loop.run_until_complete(playwright.stop())


@pytest.fixture
def page(loop, browser):
    instance = loop.run_until_complete(browser.new_page())
    yield instance
    loop.run_until_complete(instance.close())


def show(loop, page, html):
    loop.run_until_complete(page.set_content(html))


def test_detect_finds_known_widgets(loop, page):
    show(loop, page, CLEAN)
    assert loop.run_until_complete(detect(page)) is None

    show(loop, page, RECAPTCHA)
    found = loop.run_until_complete(detect(page))
    assert found and found["kind"] == "reCAPTCHA"

    show(loop, page, HCAPTCHA)
    found = loop.run_until_complete(detect(page))
    assert found and found["kind"] == "hCaptcha"


def test_detect_ignores_hidden_container(loop, page):
    show(loop, page, HIDDEN)
    assert loop.run_until_complete(detect(page)) is None


def test_detect_text_marker_can_be_switched_off(loop, page):
    show(loop, page, TEXT_ONLY)
    assert loop.run_until_complete(detect(page, CaptchaSettings())) is not None
    quiet = CaptchaSettings(check_text=False)
    assert loop.run_until_complete(detect(page, quiet)) is None


def test_own_selector_is_respected(loop, page):
    show(loop, page, "<html><body><div id='mine' style='width:50px;height:50px'>1</div></body></html>")
    settings = CaptchaSettings(selectors=("#mine",))
    found = loop.run_until_complete(detect(page, settings))
    assert found and found["kind"] == "своя проверка"


def test_guard_log_mode_only_notes(loop, page):
    show(loop, page, RECAPTCHA)
    messages = []
    guard = CaptchaGuard(CaptchaSettings(mode="log"), None, messages.append)
    assert loop.run_until_complete(guard.check(page, "steps 1")) == "logged"
    assert any("captcha" in line for line in messages)


def test_guard_stop_mode_skips_account(loop, page):
    show(loop, page, RECAPTCHA)
    guard = CaptchaGuard(CaptchaSettings(mode="stop"), None, lambda text: None)
    with pytest.raises(CaptchaSkipped):
        loop.run_until_complete(guard.check(page, "steps 1"))


def test_guard_pause_mode_waits_and_continues(loop, page):
    show(loop, page, RECAPTCHA)
    guard = CaptchaGuard(CaptchaSettings(mode="pause", pause_s=0.05), None, lambda text: None)
    assert loop.run_until_complete(guard.check(page, "steps 1")) == "paused"


def test_guard_pause_mode_hears_the_person(loop, page):
    """Пауза короткая, но кнопки в панели работают и во время неё."""
    control = RunControl(lambda text: None)
    guard = CaptchaGuard(CaptchaSettings(mode="pause", pause_s=30, poll=0.1), control,
                         lambda text: None)

    async def answer(value):
        await page.set_content(RECAPTCHA)
        task = asyncio.ensure_future(guard.check(page, "steps 1"))
        await asyncio.sleep(0.25)
        control.answer_captcha(value)
        return await task

    assert loop.run_until_complete(answer("resolved")) == "resolved"
    with pytest.raises(CaptchaSkipped):
        loop.run_until_complete(answer("skip"))
    with pytest.raises(CaptchaStop):
        loop.run_until_complete(answer("stop"))


def test_guard_pause_ends_when_widget_disappears(loop, page):
    show(loop, page, RECAPTCHA)
    guard = CaptchaGuard(CaptchaSettings(mode="pause", pause_s=30, poll=0.1), None,
                         lambda text: None)

    async def scenario():
        task = asyncio.ensure_future(guard.check(page, "steps 1"))
        await asyncio.sleep(0.3)
        await page.evaluate("document.querySelector('.g-recaptcha').remove()")
        return await task

    assert loop.run_until_complete(scenario()) == "cleared"


def test_guard_disabled_returns_immediately(loop, page):
    show(loop, page, RECAPTCHA)
    guard = CaptchaGuard(CaptchaSettings(enabled=False), None, lambda text: None)
    assert loop.run_until_complete(guard.check(page, "steps 1")) == ""


def test_guard_waits_until_widget_disappears(loop, page):
    show(loop, page, RECAPTCHA)
    control = RunControl(lambda text: None)
    guard = CaptchaGuard(CaptchaSettings(mode="wait", poll=0.2, timeout=10), control,
                         lambda text: None)

    async def scenario():
        task = asyncio.ensure_future(guard.check(page, "steps 2"))
        await asyncio.sleep(0.4)
        await page.evaluate("document.querySelector('.g-recaptcha').remove()")
        return await task

    assert loop.run_until_complete(scenario()) == "cleared"
    assert control.captcha is None


def test_guard_accepts_user_answers(loop, page):
    show(loop, page, RECAPTCHA)
    control = RunControl(lambda text: None)
    guard = CaptchaGuard(CaptchaSettings(mode="wait", poll=0.1, timeout=10), control,
                         lambda text: None)

    async def answer(value):
        task = asyncio.ensure_future(guard.check(page, "steps 3"))
        await asyncio.sleep(0.25)
        control.answer_captcha(value)
        return await task

    assert loop.run_until_complete(answer("resolved")) == "resolved"
    with pytest.raises(CaptchaSkipped):
        loop.run_until_complete(answer("skip"))
    with pytest.raises(CaptchaStop):
        loop.run_until_complete(answer("stop"))


def test_guard_timeout_is_reported(loop, page):
    show(loop, page, RECAPTCHA)
    guard = CaptchaGuard(CaptchaSettings(mode="wait", poll=0.1, timeout=0.3), None,
                         lambda text: None)
    with pytest.raises(TimeoutError):
        loop.run_until_complete(guard.check(page, "steps 4"))


def test_settings_from_dict_normalizes():
    settings = CaptchaSettings.from_dict({"mode": "nonsense", "timeout": "abc",
                                          "selectors": "#a, #b", "poll": 0.1})
    assert settings.mode == "wait"
    assert settings.timeout == 300.0
    assert settings.selectors == ("#a", "#b")
    assert settings.poll == 0.5
    assert CaptchaSettings.from_dict(None).enabled is True


def test_control_collects_events():
    seen = []
    control = RunControl(lambda text: None, seen.append)
    control.captcha_started({"kind": "reCAPTCHA", "where": "steps 1"})
    assert control.captcha["status"] == "waiting"
    assert seen[-1]["type"] == "captcha" and seen[-1]["kind"] == "reCAPTCHA"
    control.notice("нужен телефон", level="warn")
    assert seen[-1]["message"] == "нужен телефон"
    control.captcha_finished("решено", 12.3)
    assert control.captcha is None and seen[-1]["type"] == "captcha_done"
    control.request_stop()
    assert control.should_stop and control.take_answer() == "stop"
    assert control.take_answer() is None
