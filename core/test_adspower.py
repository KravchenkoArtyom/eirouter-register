import asyncio
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from core.adspower import AdsPowerError, AdsPowerProfiles, local_endpoint


def test_local_only_endpoints() -> None:
    assert local_endpoint("http://127.0.0.1:50325/", ("http",)) == "http://127.0.0.1:50325"
    for value in ("http://remote.example:50325", "http://secret@localhost:50325", "file:///tmp/test"):
        with pytest.raises(ValueError):
            local_endpoint(value, ("http",))


def test_profile_payload_uses_proxy() -> None:
    data = AdsPowerProfiles.create_payload(str(uuid.uuid4()), "http://user:p%40ss@host.test:3128")
    assert data["user_proxy_config"] == {
        "proxy_soft": "other", "proxy_type": "http", "proxy_host": "host.test",
        "proxy_port": "3128", "proxy_user": "user", "proxy_password": "p@ss",
    }
    assert data["fingerprint_config"]["automatic_timezone"] == "1"


@pytest.mark.parametrize("active", [False, True])
def test_profile_lifecycle_and_mapping(tmp_path: Path, active: bool) -> None:
    async def run() -> None:
        profiles = AdsPowerProfiles(tmp_path, "http://localhost:50325", "SECRET")
        original_page, new_page = Mock(), Mock()
        original_page.close = AsyncMock()
        new_page.close = AsyncMock()
        context = Mock()
        context.pages = [original_page]
        pw = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=AsyncMock(
            return_value=SimpleNamespace(contexts=[context]))))
        ws = {"puppeteer": "ws://127.0.0.1:4000/devtools/browser/fixture"}
        responses = [{"id": "profile-test"}, {"status": "Active", "ws": ws}] if active else [
            {"id": "profile-test"}, {"status": "Inactive"}, {"ws": ws}, {},
        ]
        profiles.request = AsyncMock(side_effect=responses)
        profile_id = str(uuid.uuid4())
        assert await profiles.launch(pw, profile_id, None, lambda _: None) is context
        mapping = tmp_path / uuid.UUID(profile_id).hex / "adspower.json"
        assert json.loads(mapping.read_text())["profile_id"] == "profile-test"
        assert "SECRET" not in mapping.read_text()
        context.pages.append(new_page)
        await profiles.close(context)
        original_page.close.assert_not_awaited()
        paths = [call.args[1] for call in profiles.request.await_args_list]
        if active:
            new_page.close.assert_awaited_once()
            assert "/api/v1/browser/stop" not in paths
        else:
            assert paths[-1] == "/api/v1/browser/stop"
        profiles.request.reset_mock(side_effect=True)
        profiles.request.side_effect = [{"status": "Active", "ws": ws}]
        await profiles.launch(pw, profile_id, None, lambda _: None)
        assert profiles.request.await_args.args[1] == "/api/v1/browser/active"
    asyncio.run(run())


def test_api_errors_do_not_echo_secrets(tmp_path: Path, monkeypatch) -> None:
    original = httpx.AsyncClient
    seen = []

    def request(req):
        seen.append(req)
        return httpx.Response(200, json={"code": -1, "msg": "SECRET"})

    def client(**kwargs):
        assert kwargs["trust_env"] is False
        return original(**kwargs, transport=httpx.MockTransport(request))

    monkeypatch.setattr("core.adspower.httpx.AsyncClient", client)
    profiles = AdsPowerProfiles(tmp_path, "http://localhost:50325", "SECRET")
    with pytest.raises(AdsPowerError) as error:
        asyncio.run(profiles.request("GET", "/api/v1/user/list"))
    assert "SECRET" not in str(error.value)
    assert seen[0].headers["Authorization"] == "Bearer SECRET"


@pytest.mark.parametrize("route", ["default", "direct", "explicit_file"])
def test_cli_adspower_route_selection(tmp_path: Path, monkeypatch, route: str) -> None:
    from providers import eirouter

    config = tmp_path / "adspower.local.json"
    config.write_text(json.dumps({"api_key": "fixture-key", "use_proxy_file": False}))
    proxy_file = tmp_path / "proxies.txt"
    proxy = "http://proxy.example:3128"
    proxy_file.write_text(proxy)
    monkeypatch.setattr(eirouter.EirouterProvider, "OUTPUT_PATH", tmp_path / "accounts.json")
    batch = AsyncMock(return_value=0)
    monkeypatch.setattr(eirouter, "register_accounts", batch)
    args = ["eirouter", "--adspower-config", str(config), "--count", "1"]
    if route == "direct":
        config.write_text(json.dumps({"api_key": "fixture-key", "use_proxy_file": True}))
        args.append("--direct")
    elif route == "explicit_file":
        args.extend(["--proxies-file", str(proxy_file)])
    monkeypatch.setattr("sys.argv", args)
    assert eirouter.main() == 0
    assert isinstance(batch.await_args.args[0].profiles, AdsPowerProfiles)
    assert batch.await_args.kwargs["proxies"] == ([proxy] if route == "explicit_file" else [])


@pytest.mark.parametrize("owned", [True, False, None])
def test_delete_only_owned_stopped_profiles(tmp_path: Path, owned) -> None:
    async def run():
        profiles = AdsPowerProfiles(tmp_path, "http://localhost:50325", "SECRET")
        profile_id = str(uuid.uuid4())
        folder = tmp_path / uuid.UUID(profile_id).hex
        folder.mkdir()
        mapping = folder / "adspower.json"
        saved = {"profile_id": "fixture", "created_by_registrar": owned}
        mapping.write_text(json.dumps(saved))
        profiles.request = AsyncMock(side_effect=[{"status": "Active"}, {"status": "Inactive"}, {}])
        await profiles.delete_created(profile_id, lambda _: None)
        if owned:
            assert profiles.request.await_args.args == ("POST", "/api/v1/user/delete")
            assert profiles.request.await_args.kwargs == {"payload": {"user_ids": ["fixture"]}}
            assert json.loads(mapping.read_text())["deleted"] is True
            with pytest.raises(ValueError, match="deleted"):
                await profiles.launch(Mock(), profile_id, None, lambda _: None)
        else:
            profiles.request.assert_not_awaited()
            assert json.loads(mapping.read_text()) == saved
    asyncio.run(run())


def test_failed_delete_preserves_mapping(tmp_path: Path) -> None:
    async def run():
        profiles = AdsPowerProfiles(tmp_path, "http://localhost:50325", "SECRET")
        profile_id = str(uuid.uuid4())
        folder = tmp_path / uuid.UUID(profile_id).hex
        folder.mkdir()
        mapping = folder / "adspower.json"
        saved = {"profile_id": "fixture", "created_by_registrar": True}
        mapping.write_text(json.dumps(saved))
        profiles.request = AsyncMock(side_effect=[{"status": "Inactive"}, AdsPowerError("failed")])
        with pytest.raises(AdsPowerError):
            await profiles.delete_created(profile_id, lambda _: None)
        assert json.loads(mapping.read_text()) == saved
    asyncio.run(run())
