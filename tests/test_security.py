"""Защита локальной панели: чужой `Host`, токен доступа и защитные заголовки.

Панель поднимается на компьютере пользователя, но её адрес может открыть и
страница из интернета (DNS rebinding), поэтому проверяем именно отказы.
"""
import pytest
from fastapi.testclient import TestClient

from webui import security
from webui.server import app

LOCAL = "http://127.0.0.1:8765"


@pytest.fixture
def guarded():
    """Панель с включённым токеном; после теста защита возвращается к исходной."""
    before = security.current()
    security.configure(token="test-token-123", hosts=security.LOOPBACK)
    yield TestClient(app, base_url=LOCAL)
    security.configure(token=before.token, hosts=before.hosts)


def test_local_requests_pass_without_token():
    client = TestClient(app, base_url=LOCAL)
    assert client.get("/api/health").status_code == 200


def test_foreign_host_is_refused():
    """Так выглядит запрос со страницы, чей домен указывает на 127.0.0.1."""
    client = TestClient(app, base_url="http://registrator.example.com:8765")
    response = client.get("/api/health")
    assert response.status_code == 403
    assert "только с этой машины" in response.json()["detail"]


def test_host_matching_ignores_port_and_case():
    access = security.Access(hosts=frozenset(security.LOOPBACK))
    assert access.host_allowed("127.0.0.1:8765")
    assert access.host_allowed("LocalHost")
    assert access.host_allowed("[::1]:8765")
    assert not access.host_allowed("example.com")
    assert not access.host_allowed(None)
    assert security.Access(hosts=frozenset()).host_allowed("что угодно")


def test_allowed_hosts_add_bind_address():
    assert "192.168.1.5" in security.allowed_hosts("192.168.1.5")
    assert security.allowed_hosts("0.0.0.0") == frozenset(security.LOOPBACK)


def test_token_link_sets_cookie_and_opens_panel(guarded):
    assert guarded.get("/api/health").status_code == 403
    response = guarded.get("/?t=test-token-123", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].endswith("/")
    assert security.COOKIE in response.cookies
    # cookie осталась в клиенте — дальше панель работает как обычно
    assert guarded.get("/api/health").json() == {"ok": True}


def test_wrong_token_is_refused(guarded):
    response = guarded.get("/?t=wrong-token", follow_redirects=False)
    assert response.status_code == 403
    assert "ссылка с токеном" in response.json()["detail"]


def test_token_comparison_is_exact():
    access = security.Access(token="a-token")
    assert access.token_ok("a-token")
    assert not access.token_ok("a-toke")
    assert not access.token_ok(None)
    assert not access.token_ok("токен по-русски")     # сравнение не падает на юникоде
    assert security.Access(token="").token_ok(None)   # без токена проверка выключена


def test_unusable_token_is_replaced(monkeypatch, capsys):
    """В ссылку и cookie попадают только «безопасные» символы."""
    monkeypatch.setenv("WEBUI_TOKEN", "секрет с пробелом")
    token = security.new_token()
    assert token != "секрет с пробелом" and security.TOKEN_CHARS.match(token)
    assert "не годится" in capsys.readouterr().out
    with pytest.raises(ValueError, match="Токен панели"):
        security.configure(token="секрет", hosts=security.LOOPBACK)


def test_post_from_another_origin_is_refused():
    client = TestClient(app, base_url=LOCAL)
    response = client.post("/api/buffer", json={"selector": "#a", "action": "click"},
                           headers={"origin": "https://evil.example"})
    assert response.status_code == 403
    assert "чужой страницы" in response.json()["detail"]


def test_post_from_panel_origin_passes():
    client = TestClient(app, base_url=LOCAL)
    created = client.post("/api/buffer", json={"selector": "#a", "action": "click"},
                          headers={"origin": "http://127.0.0.1:8765"})
    assert created.status_code == 200
    client.delete(f"/api/buffer/{created.json()['id']}")


def test_answers_carry_protective_headers():
    response = TestClient(app, base_url=LOCAL).get("/")
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_new_token_prefers_environment(monkeypatch):
    monkeypatch.setenv("WEBUI_TOKEN", "from-environment")
    assert security.new_token() == "from-environment"
    monkeypatch.delenv("WEBUI_TOKEN")
    assert len(security.new_token()) >= 20
