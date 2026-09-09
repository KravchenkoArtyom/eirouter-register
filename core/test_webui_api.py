"""Smoke-тесты WebUI: только чтение + валидация, без запуска браузера."""
from fastapi.testclient import TestClient
from webui import app
client = TestClient(app)
def test_health():
    assert client.get("/api/health").json() == {"ok": True}
def test_index_serves_html():
    response = client.get("/")
    assert response.status_code == 200
    assert "Universal Registrar" in response.text
def test_scenarios_list():
    scenarios = client.get("/api/scenarios").json()
    assert isinstance(scenarios, list) and scenarios
    assert all({"name", "url", "steps"} <= set(item) for item in scenarios)
def test_get_scenario():
    name = client.get("/api/scenarios").json()[0]["name"]
    data = client.get(f"/api/scenarios/{name}").json()
    assert isinstance(data.get("url"), str)
def test_validate():
    assert client.post("/api/scenarios/validate", json={"url": "https://x.test", "steps": []}).json() == {"errors": []}
    bad = client.post("/api/scenarios/validate", json={"steps": "oops"}).json()
    assert bad["errors"]
def test_proxies_shape():
    data = client.get("/api/proxies").json()
    assert set(data) == {"exists", "count"}


def test_inspect_unknown_session_is_404():
    assert client.get("/api/inspect/nope/shot").status_code == 404
    assert client.post("/api/inspect/nope/pick", json={}).status_code == 404
    assert client.delete("/api/inspect/nope").status_code == 404


def test_inspect_start_rejects_non_http():
    assert client.post("/api/inspect/start", json={"url": "ftp://x"}).status_code == 400
    assert client.post("/api/inspect/start", json={}).status_code == 400
