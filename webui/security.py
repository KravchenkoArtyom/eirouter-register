"""Защита локальной панели: страж по `Host`, токен доступа и заголовки.

Панель слушает `127.0.0.1`, но одной привязки к петле мало: страница из
интернета может подменить DNS своего домена на `127.0.0.1` (DNS rebinding) и
обратиться к API как к своему же origin. Поэтому запросы с чужим `Host`
отбрасываются, а состояние меняют только запросы с нашего origin.

Токен выдаётся при старте (`webui/server.py`) и попадает в браузер по ссылке
`?t=…`, дальше живёт в cookie. Токен пустой — проверка выключена (так удобно
тестам и разработке).
"""
from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass, field
from collections.abc import Iterable

from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse

COOKIE = "webui_token"
TOKEN_PARAM = "t"
# Токен уезжает и в адресную строку, и в cookie: разрешаем только те символы,
# которые там точно доедут без искажений.
TOKEN_CHARS = re.compile(r"^[A-Za-z0-9._~-]+$")
LOOPBACK: tuple[str, ...] = ("127.0.0.1", "localhost", "::1", "[::1]")
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


@dataclass
class Access:
    """Кто может обращаться к панели.

    `hosts` — допустимые имена в заголовке `Host` (пустое множество выключает
    проверку), `token` — секрет из ссылки/cookie (пустая строка выключает).
    """

    token: str = ""
    hosts: frozenset[str] = field(default_factory=lambda: frozenset(LOOPBACK))

    def host_allowed(self, header: str | None) -> bool:
        if not self.hosts:
            return True
        name = (header or "").strip().lower()
        if not name:
            return False
        if name.startswith("["):  # IPv6: [::1]:8765
            name = name.split("]")[0] + "]"
        else:
            name = name.split(":")[0]
        return name in self.hosts

    def token_ok(self, value: str | None) -> bool:
        if not self.token:
            return True
        if not value:
            return False
        # Сравниваем байты: compare_digest не работает со строками вне ASCII,
        # а токен пользователь может задать сам (WEBUI_TOKEN).
        return secrets.compare_digest(str(value).encode("utf-8"),
                                      self.token.encode("utf-8"))


_access = Access()


def current() -> Access:
    return _access


def configure(token: str | None = None, hosts: Iterable[str] | None = None) -> Access:
    """Задать защиту панели. Возвращает получившееся состояние."""
    global _access
    value = str(token or "")
    if value and not TOKEN_CHARS.match(value):
        raise ValueError("Токен панели: латинские буквы, цифры и знаки -._~")
    _access = Access(token=value,
                     hosts=frozenset(hosts) if hosts is not None else _access.hosts)
    return _access


def new_token() -> str:
    """Токен запуска: из `WEBUI_TOKEN`, иначе случайный."""
    external = os.environ.get("WEBUI_TOKEN", "").strip()
    if external and TOKEN_CHARS.match(external):
        return external
    if external:
        print("[webui] WEBUI_TOKEN не годится (нужны латиница, цифры и знаки -._~) — "
              "беру случайный токен")
    return secrets.token_urlsafe(18)


def allowed_hosts(bind_host: str) -> frozenset[str]:
    """Петля плюс адрес, на котором панель слушает (если это не петля)."""
    hosts = set(LOOPBACK)
    name = (bind_host or "").strip().lower()
    if name and name not in ("0.0.0.0", "::"):
        hosts.add(name)
    return frozenset(hosts)


def _origin_ok(origin: str | None, host_header: str | None) -> bool:
    """Origin запроса совпадает с адресом панели (или его нет вовсе)."""
    if not origin or origin == "null":
        return True
    for scheme in ("http://", "https://"):
        if origin.startswith(scheme):
            return origin[len(scheme):].lower() == (host_header or "").lower()
    return False


def install(app: FastAPI) -> None:
    """Повесить стража и защитные заголовки на приложение."""

    @app.middleware("http")
    async def guard(request, call_next):  # type: ignore[no-untyped-def]
        access = current()
        host_header = request.headers.get("host")
        if not access.host_allowed(host_header):
            return JSONResponse({"detail": "Панель принимает запросы только с этой машины"},
                                status_code=403)
        if request.method not in SAFE_METHODS and not _origin_ok(
                request.headers.get("origin"), host_header):
            return JSONResponse({"detail": "Запрос пришёл с чужой страницы"}, status_code=403)
        if access.token:
            from_query = request.query_params.get(TOKEN_PARAM)
            if from_query and access.token_ok(from_query):
                clean = request.url.remove_query_params(TOKEN_PARAM)
                response = RedirectResponse(str(clean), status_code=303)
                response.set_cookie(COOKIE, access.token, httponly=True,
                                    samesite="strict", path="/")
                return _harden(response)
            if not access.token_ok(request.cookies.get(COOKIE)):
                return JSONResponse(
                    {"detail": "Нужна ссылка с токеном — она напечатана в окне запуска панели"},
                    status_code=403)
        return _harden(await call_next(request))


def _harden(response):  # type: ignore[no-untyped-def]
    """Заголовки, которые нужны любому ответу панели."""
    headers = response.headers
    # SAMEORIGIN, а не DENY: зеркало инспектора живёт в нашем же iframe.
    headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("Referrer-Policy", "no-referrer")
    return response
