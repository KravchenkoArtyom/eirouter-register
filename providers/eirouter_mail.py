"""Mail.tm mailbox API used by the eirouter registration flow."""

import asyncio
import html as html_module
import re
import secrets
import time
from collections.abc import Callable
from urllib.parse import quote, urlsplit

import httpx

from core.tmail_web import TmailWebClient


class MailTmClient:
    BASE_URL = "https://api.mail.tm"
    LINK_PATTERN = re.compile(r"https?://[^\s\"'<>]+")
    USERNAME_ADJECTIVES = (
        "amber", "brisk", "calm", "clever", "cosmic", "fresh", "hidden",
        "lucky", "rapid", "silver", "smart", "steady", "sunny", "urban",
    )
    USERNAME_NOUNS = (
        "atlas", "comet", "fox", "harbor", "maple", "orbit", "pixel",
        "river", "signal", "spark", "stone", "vertex", "willow", "zenith",
    )

    @classmethod
    def random_username(cls) -> str:
        return (
            f"{secrets.choice(cls.USERNAME_ADJECTIVES)}"
            f"{secrets.choice(cls.USERNAME_NOUNS)}"
            f"{secrets.token_hex(4)}"
        )

    def __init__(self, client: httpx.AsyncClient, log: Callable[[str], None]) -> None:
        self.client = client
        self.log = log
        self.address = ""
        self.password = ""
        self.token = ""

    async def _request(self, method: str, path: str,
                       payload: dict[str, str] | None = None) -> dict[str, object]:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        endpoint = "/messages/{id}" if path.startswith("/messages/") else path
        try:
            response = await self.client.request(
                method, self.BASE_URL + path, json=payload, headers=headers)
        except httpx.HTTPError as error:
            self.log(f"[mail.tm] {method} {endpoint} failed: {type(error).__name__}")
            raise
        if response.status_code >= 400:
            # Do not include server bodies: validation errors can echo credentials.
            self.log(f"[mail.tm] {method} {endpoint} failed: HTTP {response.status_code}")
            raise RuntimeError(f"mail.tm {method} {path}: HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError:
            self.log(f"[mail.tm] {method} {endpoint} failed: invalid JSON (HTTP {response.status_code})")
            raise RuntimeError("mail.tm returned invalid JSON") from None
        if not isinstance(data, dict):
            self.log(f"[mail.tm] {method} {endpoint} failed: expected a JSON object")
            raise RuntimeError("mail.tm returned an invalid response")
        return data

    async def create_mailbox(self) -> str:
        self.log("[mail.tm] Loading public domains")
        domains = []
        path = "/domains"
        visited = set()
        while path:
            if path in visited:
                raise RuntimeError("mail.tm returned a repeated domains page")
            visited.add(path)
            data = await self._request("GET", path)
            domains.extend(d["domain"] for d in data.get("hydra:member", [])
                           if d.get("isActive") and not d.get("isPrivate"))
            next_page = (data.get("hydra:view") or {}).get("hydra:next")
            path = ""
            if next_page:
                parsed = urlsplit(next_page)
                if (parsed.netloc and parsed.netloc != urlsplit(self.BASE_URL).netloc
                        or parsed.path != "/domains"):
                    raise RuntimeError("mail.tm returned an invalid domains page")
                path = parsed.path + ("?" + parsed.query if parsed.query else "")
        domains = list(dict.fromkeys(domains))
        if not domains:
            self.log("[mail.tm] Mailbox creation failed: no public active domains")
            raise RuntimeError("mail.tm has no public active domains")
        domain = secrets.choice(domains)
        username = self.random_username()
        self.address = f"{username}@{domain}"
        self.password = secrets.token_urlsafe(24)
        credentials = {"address": self.address, "password": self.password}
        self.log("[mail.tm] Creating mailbox")
        created = await self._request("POST", "/accounts", credentials)
        canonical_address = created.get("address")
        if isinstance(canonical_address, str) and canonical_address:
            self.address = canonical_address
        # A newly created Mail.tm account can briefly reject token requests
        # while it is replicated between API nodes. Keep retrying for 30s.
        for attempt in range(6):
            try:
                await self.login(self.address, self.password)
                break
            except RuntimeError as error:
                if "POST /token: HTTP 401" not in str(error) or attempt == 5:
                    raise
                delay = 1.5 * (2 ** attempt)
                self.log(f"[mail.tm] Mailbox token not ready; retrying ({attempt + 2}/6)")
                await asyncio.sleep(delay)
        self.log("[mail.tm] Mailbox created")
        return self.address

    async def login(self, address: str, password: str) -> None:
        self.address, self.password = address, password
        self.log("[mail.tm] Requesting mailbox access token")
        data = await self._request("POST", "/token", {
            "address": address, "password": password})
        token = data.get("token")
        if not isinstance(token, str) or not token:
            self.log("[mail.tm] Authentication failed: response has no access token")
            raise RuntimeError("mail.tm did not return an access token")
        self.token = token

    async def wait_for_code(self, length: int = 6, timeout: float = 180,
                            interval: float = 3, sender_hint: str = "eirouter") -> str:
        deadline = time.monotonic() + timeout
        seen = set()
        async with asyncio.timeout(timeout):
            while time.monotonic() < deadline:
                listing = await self._request("GET", "/messages")
                for item in listing.get("hydra:member", []):
                    message_id = item.get("id")
                    if not message_id or message_id in seen:
                        continue
                    message = await self._request("GET", "/messages/" + quote(message_id, safe=""))
                    html = message.get("html") or []
                    if isinstance(html, str):
                        html = [html]
                    sources = [message.get("subject") or "", message.get("text") or "", *html]
                    sender = message.get("from") or {}
                    identity = " ".join([sender.get("address", ""), sender.get("name", ""), *sources])
                    seen.add(message_id)
                    if sender_hint.lower() not in identity.lower():
                        continue
                    code = TmailWebClient._pick_code([(s, 0) for s in sources], length)
                    if code:
                        return code
                await asyncio.sleep(interval)
        raise TimeoutError("eirouter verification email did not arrive")

    async def refresh(self) -> None:
        """API-клиенту обновление не нужно: список писем запрашивается каждый раз."""
        return None

    async def wait_for_link(self, timeout: float = 300, keywords=("verify",),
                            interval: float = 3) -> str:
        """Ждать письмо со ссылкой подтверждения и вернуть первую подходящую.

        Универсальные сценарии подтверждают регистрацию не только кодом, поэтому
        API-клиент умеет то же, что браузерный tmail: `mail_wait_link` и
        `mail_open_link` работают на обоих сервисах.
        """
        words = tuple(word.lower() for word in keywords if word) or ("verify",)
        deadline = time.monotonic() + timeout
        seen: set[str] = set()
        while time.monotonic() < deadline:
            listing = await self._request("GET", "/messages")
            for item in listing.get("hydra:member", []):
                message_id = item.get("id")
                if not message_id or message_id in seen:
                    continue
                seen.add(message_id)
                message = await self._request("GET", "/messages/" + quote(message_id, safe=""))
                body = message.get("html") or []
                if isinstance(body, str):
                    body = [body]
                text = html_module.unescape(" ".join([message.get("text") or "", *body]))
                for url in self.LINK_PATTERN.findall(text):
                    url = url.rstrip(").,;'\"")
                    lowered = url.lower()
                    if any(hint in lowered for hint in TmailWebClient.EXCLUDE_PATH_HINTS):
                        continue
                    if any(word in lowered for word in words):
                        return url
            await asyncio.sleep(interval)
        raise TimeoutError("ссылка подтверждения не пришла за отведённое время")
