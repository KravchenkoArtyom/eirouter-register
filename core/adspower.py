"""AdsPower Local API profile lifecycle and Playwright CDP attachment."""

import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx
from playwright.async_api import BrowserContext, Page, Playwright


class AdsPowerError(RuntimeError):
    pass


def local_endpoint(value: str, schemes: tuple[str, ...]) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in schemes or parsed.hostname not in {"127.0.0.1", "localhost", "::1", "local.adspower.com"}:
        raise ValueError("AdsPower endpoints must use a local address")
    if parsed.username or parsed.password:
        raise ValueError("AdsPower endpoint must not contain credentials")
    return value.rstrip("/")


class AdsPowerProfiles:
    def __init__(self, root: Path, base_url: str, api_key: str, profile_id: str | None = None) -> None:
        self.root = root
        self.base_url = local_endpoint(base_url, ("http", "https"))
        self.api_key = api_key
        self.profile_id = profile_id
        self._last_request = 0.0
        self._sessions: dict[BrowserContext, tuple[str, bool, set[Page]]] = {}

    async def request(self, method: str, path: str, *, params: dict[str, object] | None = None,
                      payload: dict[str, object] | None = None) -> dict[str, object]:
        await asyncio.sleep(max(0, 1 - (time.monotonic() - self._last_request)))
        self._last_request = time.monotonic()
        async with httpx.AsyncClient(base_url=self.base_url, trust_env=False, timeout=60,
                                     headers={"Authorization": f"Bearer {self.api_key}"}) as client:
            response = await client.request(method, path, params=params, json=payload)
        if response.status_code != 200:
            raise AdsPowerError(f"AdsPower {path}: HTTP {response.status_code}")
        data = response.json()
        if not isinstance(data, dict) or data.get("code") != 0:
            code = data.get("code") if isinstance(data, dict) else "invalid"
            raw_message = data.get("msg") if isinstance(data, dict) else ""
            if isinstance(raw_message, str) and "exceeds the limit" in raw_message.lower():
                message = "AdsPower profile limit reached; delete an unused profile"
            else:
                message = "check the AdsPower application"
            raise AdsPowerError(f"AdsPower {path} failed (code {code}): {message}")
        result = data.get("data") or {}
        if not isinstance(result, dict):
            raise AdsPowerError(f"AdsPower {path} returned an invalid data object")
        return result

    @staticmethod
    def create_payload(profile_id: str, proxy: str | None) -> dict[str, object]:
        proxy_config = {"proxy_soft": "no_proxy"}
        if proxy:
            parsed = urlsplit(proxy)
            proxy_config = {"proxy_soft": "other", "proxy_type": parsed.scheme,
                            "proxy_host": parsed.hostname, "proxy_port": str(parsed.port),
                            "proxy_user": unquote(parsed.username or ""),
                            "proxy_password": unquote(parsed.password or "")}
        return {"name": f"eirouter-{uuid.UUID(profile_id).hex[:12]}", "group_id": "0",
                "user_proxy_config": proxy_config,
                "fingerprint_config": {"automatic_timezone": "1", "language_switch": "1",
                                       "webrtc": "disabled", "random_ua": {
                                           "ua_browser": ["chrome"], "ua_system_version": ["Windows 10", "Windows 11"]}}}

    async def launch(self, pw: Playwright, profile_id: str, proxy: str | None,
                     log: Callable[[str], None], *, headless: bool = False) -> BrowserContext:
        folder = self.root / uuid.UUID(profile_id).hex
        folder.mkdir(parents=True, exist_ok=True)
        mapping = folder / "adspower.json"
        route_id = hashlib.sha256((proxy or "direct").encode()).hexdigest()
        if mapping.exists():
            saved = json.loads(mapping.read_text(encoding="utf-8"))
            if saved.get("deleted"):
                raise ValueError("This profile was deleted after saving credentials; use the saved login and key")
            adspower_id = saved["profile_id"]
            if self.profile_id and self.profile_id != adspower_id:
                raise ValueError("The saved account belongs to a different AdsPower profile")
            if saved["route_id"] != route_id:
                raise ValueError("Resume with the same proxy selection as registration")
        else:
            adspower_id = self.profile_id
            if adspower_id:
                if proxy:
                    raise ValueError("Configure an existing profile's proxy in AdsPower")
            else:
                try:
                    created = await self.request("POST", "/api/v1/user/create",
                                                 payload=self.create_payload(profile_id, proxy))
                except AdsPowerError as error:
                    if "profile limit reached" not in str(error).lower():
                        raise
                    removed = await self.cleanup_owned_profiles(log)
                    if not removed:
                        raise AdsPowerError(
                            "AdsPower profile limit reached and no stopped registrar profiles are available"
                        )
                    created = await self.request("POST", "/api/v1/user/create",
                                                 payload=self.create_payload(profile_id, proxy))
                adspower_id = created.get("id")
                if not isinstance(adspower_id, str) or not adspower_id:
                    raise AdsPowerError("AdsPower did not return a profile ID")
            mapping.write_text(json.dumps({"profile_id": adspower_id, "route_id": route_id,
                                           "created_by_registrar": self.profile_id is None}, indent=2), encoding="utf-8")
        active = await self.request("GET", "/api/v1/browser/active", params={"user_id": adspower_id})
        was_active = active.get("status") == "Active"
        data = active if was_active else await self.request("GET", "/api/v1/browser/start", params={
            "user_id": adspower_id, "ip_tab": 0, "open_tabs": 1, "headless": int(headless)})
        try:
            endpoint = (data.get("ws") or {}).get("puppeteer")
            if not isinstance(endpoint, str):
                raise AdsPowerError("AdsPower did not return a CDP endpoint")
            local_endpoint(endpoint, ("ws", "wss"))
            browser = await pw.chromium.connect_over_cdp(endpoint, timeout=30_000)
            if not browser.contexts:
                raise AdsPowerError("AdsPower browser has no default context")
            context = browser.contexts[0]
            self._sessions[context] = (adspower_id, not was_active, set(context.pages))
            context.set_default_timeout(30_000)
            context.set_default_navigation_timeout(120_000)
            log(f"[adspower] Connected to profile {adspower_id}")
            return context
        except BaseException:
            if not was_active:
                await self.request("GET", "/api/v1/browser/stop", params={"user_id": adspower_id})
            raise

    async def _reclaim_oldest_created(self, log: Callable[[str], None]) -> None:
        if not await self.cleanup_owned_profiles(log, limit=1):
            raise AdsPowerError("AdsPower profile limit reached and no stopped registrar profile is available")

    async def cleanup_owned_profiles(self, log: Callable[[str], None], *,
                                     extra_roots: tuple[Path, ...] = (),
                                     limit: int | None = None) -> int:
        """Delete only locally owned profiles confirmed stopped by AdsPower."""
        candidates = []
        roots = dict.fromkeys(root.resolve() for root in (self.root, *extra_roots))
        for root in roots:
            for mapping in root.glob("*/adspower.json"):
                try:
                    saved = json.loads(mapping.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if (isinstance(saved, dict) and saved.get("created_by_registrar") is True
                        and not saved.get("deleted") and isinstance(saved.get("profile_id"), str)
                        and saved["profile_id"]):
                    candidates.append((mapping.stat().st_mtime, mapping, saved))
        if not candidates:
            return 0

        # Old local mappings may outlive profiles deleted in the AdsPower UI.
        existing = set()
        page = 1
        while True:
            data = await self.request("GET", "/api/v1/user/list", params={"page": page, "page_size": 100})
            rows = data.get("list")
            if not isinstance(rows, list) or any(
                    not isinstance(row, dict) or not isinstance(row.get("user_id"), str) for row in rows):
                raise AdsPowerError("AdsPower returned an invalid profile list; cleanup stopped")
            ids = {row["user_id"] for row in rows}
            if rows and not ids.difference(existing):
                raise AdsPowerError("AdsPower repeated a profile list page; cleanup stopped")
            existing.update(ids)
            if len(rows) < 100:
                break
            page += 1

        protected = {self.profile_id, *(session[0] for session in self._sessions.values())}
        removed = 0
        for _, mapping, saved in sorted(candidates):
            profile = saved["profile_id"]
            if profile in protected:
                continue
            if profile in existing:
                state = await self.request("GET", "/api/v1/browser/active", params={"user_id": profile})
                if state.get("status") != "Inactive":
                    log(f"[adspower] Retained profile {profile}: not confirmed stopped")
                    continue
                await self.request("POST", "/api/v1/user/delete", payload={"user_ids": [profile]})
                existing.remove(profile)
                removed += 1
                log(f"[adspower] Deleted old stopped registrar profile {profile}")
            saved["deleted"] = True
            temporary = mapping.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(saved, indent=2), encoding="utf-8")
            temporary.replace(mapping)
            if limit is not None and removed >= limit:
                break
        return removed

    async def close(self, context: BrowserContext) -> None:
        profile_id, started_here, original_pages = self._sessions.pop(context)
        if started_here:
            await self.request("GET", "/api/v1/browser/stop", params={"user_id": profile_id})
        else:
            for page in context.pages:
                if page not in original_pages:
                    await page.close()

    async def delete_created(self, profile_id: str, log: Callable[[str], None]) -> None:
        mapping = self.root / uuid.UUID(profile_id).hex / "adspower.json"
        saved = json.loads(mapping.read_text(encoding="utf-8"))
        if saved.get("created_by_registrar") is not True or saved.get("deleted"):
            return
        adspower_id = saved["profile_id"]
        # AdsPower can acknowledge stop before the browser has actually exited.
        deadline = time.monotonic() + 30
        while True:
            state = await self.request("GET", "/api/v1/browser/active", params={"user_id": adspower_id})
            if state.get("status") == "Inactive":
                break
            if state.get("status") != "Active" or time.monotonic() >= deadline:
                raise AdsPowerError("Profile is not stopped; credentials are saved, deletion was skipped")
            await asyncio.sleep(1)
        await self.request("POST", "/api/v1/user/delete", payload={"user_ids": [adspower_id]})
        saved["deleted"] = True
        temporary = mapping.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(saved, indent=2), encoding="utf-8")
        temporary.replace(mapping)
        log(f"[adspower] Deleted profile {adspower_id}; saved credentials are retained")
