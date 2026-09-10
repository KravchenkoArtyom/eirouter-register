"""Смоук-тесты API панели: чтение, валидация и файловые операции.

Файлы, которые тесты меняют (список прокси, настройки панели), подменяются на
временные — рабочие proxies.txt и webui.local.json не трогаются.
"""
import pytest
from fastapi.testclient import TestClient

from universal.scenarios import TRASH_DIR
from webui.server import app

client = TestClient(app)
TEMP_NAME = "pytest-temp-scenario"
TEMP_MAIL = "pytest-temp-service"


def test_health():
    assert client.get("/api/health").json() == {"ok": True}


def test_index_serves_html():
    response = client.get("/")
    assert response.status_code == 200
    assert "Регистратор" in response.text


def test_static_assets_served():
    for path in ("/static/styles.css", "/static/picker.js", "/static/how-it-works.svg",
                 "/static/js/main.js", "/static/js/steps.js", "/static/js/runs.js",
                 "/static/js/mail.js", "/static/js/proxies.js", "/static/js/overview.js"):
        assert client.get(path).status_code == 200


def test_index_shows_all_sections():
    page = client.get("/").text
    for view in ("overview", "run", "scenario", "mail", "proxy", "inspector", "accounts"):
        assert f'data-view="{view}"' in page
    assert 'id="captchaBar"' in page          # полоса вызова человека к капче
    assert 'id="proxyDrop"' in page           # файл прокси можно перетащить
    assert 'src="/static/how-it-works.svg"' in page


def test_theme_switch_is_shipped():
    page = client.get("/").text
    assert 'id="themeSwitch"' in page
    assert 'data-theme-choice="dark"' in page and 'data-theme-choice="auto"' in page
    styles = client.get("/static/styles.css").text
    assert ':root[data-theme="dark"]' in styles
    assert "color-scheme: dark" in styles
    script = client.get("/static/js/theme.js").text
    assert "prefers-color-scheme: dark" in script and "webui-theme" in script


def test_environment_shape():
    data = client.get("/api/environment").json()
    assert set(data) == {"adspower", "proxies", "proxies_file", "mail_services"}


def test_action_catalog_has_mail_and_guard_groups():
    catalog = client.get("/api/actions").json()
    names = {action["name"] for action in catalog["actions"]}
    assert {"fill", "click", "dom", "mail_create", "mail_wait_code",
            "mail_wait_link", "mail_open_link", "mail_refresh",
            "captcha_wait", "wait_random", "notify"} <= names
    groups = {group["id"] for group in catalog["groups"]}
    assert {"mail", "guard"} <= groups
    assert "{email}" in catalog["placeholders"]


def test_scenarios_list():
    scenarios = client.get("/api/scenarios").json()
    assert isinstance(scenarios, list) and scenarios
    assert all({"name", "url", "steps", "mail_steps"} <= set(item) for item in scenarios)


def test_get_scenario():
    name = client.get("/api/scenarios").json()[0]["name"]
    data = client.get(f"/api/scenarios/{name}").json()
    assert isinstance(data.get("url"), str)


def test_missing_scenario_is_404():
    assert client.get("/api/scenarios/definitely-missing").status_code == 404


def test_validate_reports_errors():
    ok = client.post("/api/scenarios/validate",
                     json={"url": "https://x.test", "steps": []}).json()
    assert ok == {"errors": []}
    bad = client.post("/api/scenarios/validate", json={"steps": "oops"}).json()
    assert bad["errors"]
    unknown = client.post("/api/scenarios/validate", json={
        "url": "https://x.test", "steps": [{"action": "no_such_action"}]}).json()
    assert any("no_such_action" in error for error in unknown["errors"])
    no_selector = client.post("/api/scenarios/validate", json={
        "url": "https://x.test", "steps": [{"action": "fill", "value": "{email}"}]}).json()
    assert no_selector["errors"]


def test_mail_steps_validate_in_any_section():
    payload = {"url": "https://x.test",
               "steps": [{"action": "mail_create", "service": "mail.tm"}],
               "verification": {"wait": [{"action": "mail_wait_code", "length": 6}]},
               "success": [{"action": "mail_open_link", "target": "new_tab"}]}
    assert client.post("/api/scenarios/validate", json=payload).json() == {"errors": []}
    payload["success"][0]["target"] = "sideways"
    assert client.post("/api/scenarios/validate", json=payload).json()["errors"]


def test_scenario_lifecycle_create_update_delete():
    client.delete(f"/api/scenarios/{TEMP_NAME}")
    created = client.post("/api/scenarios", json={
        "name": TEMP_NAME, "url": "https://example.test/register",
        "mail": "tmail", "template": "email_code"})
    assert created.status_code == 200, created.text
    assert created.json()["file"] == f"{TEMP_NAME}.json"
    assert any(item["name"] == TEMP_NAME for item in client.get("/api/scenarios").json())

    duplicate = client.post("/api/scenarios", json={"name": TEMP_NAME, "url": "https://x.test"})
    assert duplicate.status_code == 409

    data = client.get(f"/api/scenarios/{TEMP_NAME}").json()
    assert data["steps"][0]["action"] == "mail_create"
    data["steps"].append({"action": "mail_refresh"})
    saved = client.put(f"/api/scenarios/{TEMP_NAME}", json=data)
    assert saved.status_code == 200
    assert client.get(f"/api/scenarios/{TEMP_NAME}").json()["steps"][-1]["action"] == "mail_refresh"

    broken = client.put(f"/api/scenarios/{TEMP_NAME}", json={"url": ""})
    assert broken.status_code == 400

    removed = client.delete(f"/api/scenarios/{TEMP_NAME}")
    assert removed.status_code == 200
    assert client.get(f"/api/scenarios/{TEMP_NAME}").status_code == 404
    for leftover in TRASH_DIR.glob(f"{TEMP_NAME}_*.json"):
        leftover.unlink()


def test_run_rejects_bad_input():
    assert client.post("/api/runs", json={}).status_code == 400
    name = client.get("/api/scenarios").json()[0]["name"]
    assert client.post("/api/runs", json={"scenario": name, "count": 0}).status_code == 400
    assert client.post("/api/runs", json={"scenario": name, "mail": "carrier-pigeon"}).status_code == 400
    assert client.get("/api/runs/unknown").status_code == 404


def test_accounts_shape():
    data = client.get("/api/accounts").json()
    assert isinstance(data["files"], list)


def test_element_buffer_roundtrip():
    before = len(client.get("/api/buffer").json())
    assert client.post("/api/buffer", json={"selector": ""}).status_code == 400
    item = client.post("/api/buffer", json={
        "selector": "#pytest-email", "action": "fill", "value": "{email}",
        "tag": "input", "url": "https://example.test"}).json()
    assert item["selector"] == "#pytest-email"
    assert len(client.get("/api/buffer").json()) == before + 1
    updated = client.patch(f"/api/buffer/{item['id']}", json={"note": "проверка"}).json()
    assert updated["note"] == "проверка"
    assert client.delete(f"/api/buffer/{item['id']}").status_code == 200
    assert client.delete(f"/api/buffer/{item['id']}").status_code == 404
    assert len(client.get("/api/buffer").json()) == before


@pytest.mark.parametrize("method,path", [
    ("get", "/api/inspect/nope/state"),
    ("get", "/api/inspect/nope/shot"),
    ("get", "/api/inspect/nope/mirror"),
])
def test_inspect_unknown_session_is_404(method, path):
    assert getattr(client, method)(path).status_code == 404


def test_inspect_unknown_session_posts_are_404():
    assert client.post("/api/inspect/nope/pick", json={}).status_code == 404
    assert client.post("/api/inspect/nope/verify", json={"selector": "a"}).status_code == 404
    assert client.post("/api/inspect/nope/act", json={"action": "reload"}).status_code == 404
    assert client.delete("/api/inspect/nope").status_code == 404


def test_inspect_start_rejects_non_http():
    assert client.post("/api/inspect/start", json={"url": "ftp://x"}).status_code == 400
    assert client.post("/api/inspect/start", json={}).status_code == 400


# ---- почтовые сервисы ----
def test_mail_services_list_includes_builtins():
    services = client.get("/api/mail-services").json()
    ids = {item["id"] for item in services}
    assert {"auto", "none", "tmail", "mail.tm"} <= ids
    catalog = client.get("/api/mail/services").json()
    assert catalog["sections"] == ["create", "refresh", "open_message"]
    assert "clipboard" in catalog["address_sources"]
    assert client.get("/api/mail/templates").json()


def test_mail_service_lifecycle():
    client.delete(f"/api/mail/services/{TEMP_MAIL}")
    created = client.post("/api/mail/services", json={
        "name": TEMP_MAIL, "url": "https://mail.test/inbox", "template": "button"})
    assert created.status_code == 200, created.text
    try:
        assert created.json()["file"] == f"{TEMP_MAIL}.json"
        assert any(item["id"] == TEMP_MAIL
                   for item in client.get("/api/mail-services").json())

        data = client.get(f"/api/mail/services/{TEMP_MAIL}").json()
        assert data["create"]
        data["refresh"].append({"action": "wait", "seconds": 1})
        assert client.put(f"/api/mail/services/{TEMP_MAIL}", json=data).status_code == 200
        assert len(client.get(f"/api/mail/services/{TEMP_MAIL}").json()["refresh"]) == 2

        data["url"] = "почта.локально"
        assert client.put(f"/api/mail/services/{TEMP_MAIL}", json=data).status_code == 400

        # сценарий может ссылаться на свой сервис
        payload = {"url": "https://x.test",
                   "steps": [{"action": "mail_create", "service": TEMP_MAIL}]}
        assert client.post("/api/scenarios/validate", json=payload).json() == {"errors": []}
    finally:
        removed = client.delete(f"/api/mail/services/{TEMP_MAIL}")
        assert removed.status_code == 200
    assert client.get(f"/api/mail/services/{TEMP_MAIL}").status_code == 404


def test_builtin_service_is_not_editable():
    assert client.get("/api/mail/services/tmail").status_code == 400
    assert client.post("/api/mail/services", json={"name": "tmail"}).status_code == 400


# ---- прокси ----
@pytest.fixture
def proxies_file(tmp_path, monkeypatch):
    target = tmp_path / "proxies.txt"
    monkeypatch.setattr("webui.api.proxies.PROXIES_FILE", target)
    return target


def test_proxy_list_roundtrip(proxies_file):
    assert client.get("/api/proxies").json()["count"] == 0
    saved = client.put("/api/proxies", json={"text": "1.1.1.1:8000\nuser:pass@2.2.2.2:8001"})
    assert saved.status_code == 200
    assert saved.json()["count"] == 2
    assert "user" not in " ".join(saved.json()["items"])   # пароли не возвращаем

    state = client.get("/api/proxies").json()
    assert state["count"] == 2 and state["exists"]
    assert [item["id"] for item in state["rotations"]][0] == "per_account"

    bad = client.put("/api/proxies", json={"text": "1.1.1.1:8000\nсовсем не прокси"})
    assert bad.status_code == 400
    forced = client.put("/api/proxies", json={"text": "1.1.1.1:8000\nсовсем не прокси",
                                              "ignore_errors": True})
    assert forced.json()["count"] == 1 and forced.json()["skipped"]

    assert client.delete("/api/proxies").json()["count"] == 0
    assert client.get("/api/proxies").json()["count"] == 0


def test_proxy_requests_are_validated(proxies_file):
    assert client.put("/api/proxies", json={}).status_code == 400
    assert client.post("/api/proxies/check", json={}).status_code == 400


# ---- настройки панели ----
@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    target = tmp_path / "webui.local.json"
    monkeypatch.setattr("webui.config.CONFIG_FILE", target)
    return target


def test_settings_defaults_and_save(settings_file):
    data = client.get("/api/settings").json()
    assert set(data["settings"]) == {"captcha", "delays", "proxy", "notify"}
    assert data["options"]["captcha_modes"][0] == "wait"

    saved = client.put("/api/settings", json={
        "captcha": {"mode": "pause", "timeout": 42, "selectors": "#a, #b"},
        "delays": {"step_min_ms": 10, "step_max_ms": 20},
        "proxy": {"mode": "random", "ban_after": 3}})
    assert saved.status_code == 200
    settings = saved.json()["settings"]
    assert settings["captcha"]["mode"] == "pause" and settings["captcha"]["timeout"] == 42
    assert settings["captcha"]["selectors"] == ["#a", "#b"]
    assert settings["delays"]["step_max_ms"] == 20
    assert settings["proxy"] == {"mode": "random", "ban_after": 3, "source": "file"}
    assert settings_file.exists()
    assert client.get("/api/settings").json()["settings"]["captcha"]["mode"] == "pause"


def test_settings_reject_unknown_sections(settings_file):
    assert client.put("/api/settings", json={"whatever": 1}).status_code == 400
    fixed = client.put("/api/settings", json={"captcha": {"mode": "звонок другу"}}).json()
    assert fixed["settings"]["captcha"]["mode"] == "wait"


# ---- запуск ----
def test_run_validates_network_options():
    name = client.get("/api/scenarios").json()[0]["name"]
    assert client.post("/api/runs", json={"scenario": name,
                                          "proxy_mode": "телепатия"}).status_code == 400
    assert client.post("/api/runs", json={"scenario": name,
                                          "rotation": "по-настроению"}).status_code == 400
    assert client.post("/api/runs", json={"scenario": name, "proxy_mode": "single",
                                          "proxy": ""}).status_code == 400
    assert client.post("/api/runs", json={"scenario": name, "proxy_mode": "single",
                                          "proxy": "socks5://1.1.1.1:1080"}).status_code == 400
    assert client.post("/api/runs", json={"scenario": name, "proxy_mode": "list",
                                          "proxies_text": "мусор"}).status_code == 400


def test_captcha_answer_needs_known_run():
    assert client.post("/api/runs/unknown/captcha", json={"action": "resolved"}).status_code == 404
