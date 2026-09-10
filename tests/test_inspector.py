"""Инспектор: сборка зеркала, его безопасность и временный буфер элементов.

Зеркало — чужая страница, отданная с нашего origin, поэтому очистке DOM
посвящён отдельный набор проверок: скрипт `MIRROR_JS` выполняется в настоящем
Chromium на нарочно «злой» странице.
"""
import re

import pytest
from fastapi.testclient import TestClient

from webui.api import inspector as inspector_api
from webui.element_buffer import ElementBuffer
from webui.inspect_session import (MIRROR_JS, PICK_JS, inject_picker, mirror_csp,
                                   new_nonce)
from webui.server import app
from webui.settings import STATIC_DIR

LOCAL = "http://127.0.0.1:8765"

# Страница, которая очень хочет что-нибудь выполнить у нас в панели.
NASTY_PAGE = """<!DOCTYPE html><html><head>
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'">
  <link rel="preload" as="script" href="/attack.js">
  <base href="https://site.example/old/">
</head><body onload="window.stolen = 1">
  <script>window.executed = true;</script>
  <noscript>без скриптов</noscript>
  <a id="bad" href="javascript:alert(1)" onclick="alert(2)">ссылка</a>
  <form action="javascript:alert(3)"><input id="email" name="email" value="">
    <input id="agree" type="checkbox">
    <select id="plan"><option value="free">free</option><option value="pro">pro</option></select>
    <button formaction="javascript:alert(4)" onmouseover="alert(5)">Отправить</button>
  </form>
  <iframe src="https://ads.example/frame" srcdoc="<script>alert(6)</script>"></iframe>
  <img src="x.png" onerror="alert(7)" poster="javascript:alert(8)">
</body></html>"""


def test_injects_script_before_body_close():
    result = inject_picker("<html><body><p>x</p></body></html>", "console.log(1)", "a1", 2)
    assert result.index("console.log(1)") < result.index("</body>")
    assert 'data-sid="a1"' in result and 'data-revision="2"' in result


def test_injects_script_when_body_is_missing():
    assert inject_picker("<p>x</p>", "console.log(1)").endswith("</script>")


def test_picker_script_is_shipped_and_inlined():
    script = (STATIC_DIR / "picker.js").read_text(encoding="utf-8")
    # Ссылку использовать нельзя: в зеркале стоит <base href> сайта.
    assert "</script>" not in script
    assert "webui-picker" in script
    assert inject_picker("<body></body>", script).count("webui-picker") >= 1


def test_mirror_script_cleans_page_and_sets_base():
    for fragment in ("cloneNode", "script, noscript", "document.baseURI", "iframe"):
        assert fragment in MIRROR_JS
    assert "elementFromPoint" in PICK_JS


# ---- зеркало в настоящем браузере ----
@pytest.fixture(scope="module")
def mirror_html():
    """Отзеркалить «злую» страницу настоящим Chromium (иначе тест пропускается)."""
    playwright = pytest.importorskip("playwright.sync_api")
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.route("https://site.example/**", lambda route: route.fulfill(
                    content_type="text/html", body=NASTY_PAGE))
                page.goto("https://site.example/signup", wait_until="domcontentloaded")
                page.fill("#email", "user@example.test")
                page.check("#agree")
                page.select_option("#plan", "pro")
                return page.evaluate(MIRROR_JS)
            finally:
                browser.close()
    except Exception as error:  # браузер не установлен — не наша вина
        pytest.skip(f"браузер Playwright недоступен: {error}")


def test_mirror_drops_scripts_and_handlers(mirror_html):
    assert "<script" not in mirror_html.lower()
    assert "window.executed" not in mirror_html
    assert "<noscript" not in mirror_html.lower()
    assert not re.search(r"\son\w+\s*=", mirror_html), "остался inline-обработчик"
    assert "srcdoc" not in mirror_html.lower()
    live = re.compile(r'\s(?:href|src|action|formaction|data|poster)="\s*javascript:', re.I)
    assert not live.search(mirror_html), "javascript: остался в рабочем атрибуте"
    # опасные значения сохранены как данные — их видно в панели, но не выполнить
    assert 'data-mirror-href="javascript:alert(1)"' in mirror_html
    assert "data-mirror-formaction" in mirror_html and "data-mirror-poster" in mirror_html


def test_mirror_keeps_page_state_and_base(mirror_html):
    assert 'value="user@example.test"' in mirror_html
    assert 'checked="checked"' in mirror_html
    assert mirror_html.count('selected="selected"') == 1
    # <base> один и указывает туда же, куда указывал у сайта: стили и картинки
    # в зеркале грузятся по тем же адресам, что и на настоящей странице
    assert mirror_html.count("<base ") == 1
    assert '<base href="https://site.example/old/" target="_self">' in mirror_html
    assert 'data-mirror-src="https://ads.example/frame"' in mirror_html
    assert 'rel="preload"' not in mirror_html
    assert "Content-Security-Policy" not in mirror_html


# ---- политика безопасности зеркала ----
def test_mirror_csp_allows_only_our_script():
    nonce = new_nonce()
    policy = mirror_csp(nonce)
    assert f"script-src 'nonce-{nonce}'" in policy
    assert "default-src 'none'" in policy
    assert "connect-src 'none'" in policy and "form-action 'none'" in policy
    assert new_nonce() != nonce


def test_mirror_answer_carries_nonce_and_csp(monkeypatch):
    """Скрипт выбора элементов подписан тем же nonce, что и заголовок ответа."""

    class FakeSession:
        revision = 3
        idle_seconds = 0

        def mirror(self):
            return "<html><body><p>страница</p></body></html>"

    monkeypatch.setitem(inspector_api._sessions, "fake", FakeSession())
    response = TestClient(app, base_url=LOCAL).get("/api/inspect/fake/mirror")
    assert response.status_code == 200
    nonce = re.search(r"script-src 'nonce-([^']+)'",
                      response.headers["content-security-policy"]).group(1)
    assert f'nonce="{nonce}"' in response.text
    assert response.headers["cache-control"] == "no-store"
    assert 'data-revision="3"' in response.text


def test_mirror_lives_in_a_sandboxed_frame():
    page = TestClient(app, base_url=LOCAL).get("/").text
    frame = page[page.index('id="mirror"') - 200:page.index('id="mirror"') + 200]
    assert 'sandbox="allow-scripts"' in frame
    assert 'referrerpolicy="no-referrer"' in frame


def test_idle_sessions_are_closed(monkeypatch):
    class FakeSession:
        def __init__(self, idle):
            self.idle_seconds = idle
            self.closed = False

        def close(self):
            self.closed = True

    monkeypatch.setattr(inspector_api, "INSPECT_IDLE_SECONDS", 60)
    fresh, stale = FakeSession(1), FakeSession(120)
    monkeypatch.setitem(inspector_api._sessions, "fresh", fresh)
    monkeypatch.setitem(inspector_api._sessions, "stale", stale)
    inspector_api._drop_idle()
    assert stale.closed and not fresh.closed
    assert "stale" not in inspector_api._sessions
    inspector_api.shutdown()
    assert fresh.closed and not inspector_api._sessions


# ---- буфер элементов ----
def test_element_buffer_survives_reload(tmp_path):
    path = tmp_path / "elements.json"
    buffer = ElementBuffer(path)
    assert buffer.all() == []
    item = buffer.add({"selector": "#email", "action": "fill", "value": "{email}",
                       "ignored": "не сохраняем"})
    assert item["id"] and "ignored" not in item
    assert buffer.update(item["id"], {"note": "почта"})["note"] == "почта"
    assert buffer.update("missing", {"note": "x"}) is None

    reopened = ElementBuffer(path)
    assert [entry["selector"] for entry in reopened.all()] == ["#email"]
    assert reopened.remove(item["id"]) is True
    assert reopened.remove(item["id"]) is False
    reopened.add({"selector": "#password"})
    assert reopened.clear() == 1
    assert ElementBuffer(path).all() == []


def test_element_buffer_ignores_broken_file(tmp_path):
    path = tmp_path / "elements.json"
    path.write_text("{ не json", encoding="utf-8")
    assert ElementBuffer(path).all() == []
