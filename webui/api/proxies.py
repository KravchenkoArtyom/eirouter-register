"""API прокси: список, файл, ротация и проверка связи.

Строки прокси содержат логины и пароли, поэтому сам текст файла отдаётся
только по явному запросу (`?text=1` — когда человек открыл редактор списка);
в остальных ответах адреса маскируются `redact_proxy`.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from core.proxy_pool import (ROTATIONS, ROTATION_LABELS, load_list, parse_lines,
                             is_socks, playwright_proxy, redact_proxy, save_list)
from webui.settings import PROXIES_FILE

router = APIRouter(tags=["proxies"])

CHECK_URL = "https://api.ipify.org?format=json"
CHECK_LIMIT = 40


def _state(text: str | None = None, include_text: bool = True) -> dict[str, Any]:
    body = PROXIES_FILE.read_text(encoding="utf-8-sig") if PROXIES_FILE.exists() else ""
    if text is not None:
        body = text
    proxies, errors = parse_lines(body)
    return {"exists": PROXIES_FILE.exists(), "file": PROXIES_FILE.name,
            "count": len(proxies), "text": body if include_text else "",
            "has_text": bool(body.strip()),
            "items": [redact_proxy(proxy) for proxy in proxies], "errors": errors,
            "rotations": [{"id": key, "label": ROTATION_LABELS[key]} for key in ROTATIONS]}


@router.get("/api/proxies")
def api_proxies(text: int = 0) -> dict[str, Any]:
    """Текущий список: адреса без пароля, ошибки строк и режимы ротации.

    `?text=1` добавляет сам текст файла — это нужно только редактору списка.
    """
    return _state(include_text=bool(text))


@router.put("/api/proxies")
def api_save_proxies(payload: dict[str, Any]) -> dict[str, Any]:
    """Сохранить список в файл. Текст приходит из поля ввода или из файла,
    который пользователь перетащил в панель — читает его сам браузер."""
    text = payload.get("text")
    if not isinstance(text, str):
        raise HTTPException(400, "Нужно поле text со списком прокси")
    proxies, errors = parse_lines(text)
    if errors and not payload.get("ignore_errors"):
        raise HTTPException(400, "; ".join(errors[:5]))
    try:
        count = save_list(PROXIES_FILE, proxies)
    except (OSError, ValueError) as error:
        raise HTTPException(500, str(error))
    return {"ok": True, "count": count, "skipped": errors,
            "items": [redact_proxy(proxy) for proxy in proxies]}


@router.delete("/api/proxies")
def api_clear_proxies() -> dict[str, Any]:
    if PROXIES_FILE.exists():
        try:
            save_list(PROXIES_FILE, [])
        except OSError as error:
            raise HTTPException(500, str(error))
    return {"ok": True, "count": 0}


async def _check_one(proxy: str, timeout: float) -> dict[str, Any]:
    label = redact_proxy(proxy)
    if is_socks(proxy):
        try:
            import socksio  # noqa: F401  (httpx[socks])
        except ImportError:
            return {"proxy": label, "ok": False,
                    "error": "socks5 не проверить: поставьте httpx[socks]. "
                             "В браузере такой адрес всё равно работает"}
    settings = playwright_proxy(proxy)
    url = settings["server"]
    if settings.get("username"):
        scheme, rest = url.split("://", 1)
        url = f"{scheme}://{settings['username']}:{settings.get('password', '')}@{rest}"
    started = asyncio.get_running_loop().time()
    try:
        async with httpx.AsyncClient(proxy=url, timeout=timeout, trust_env=False) as client:
            response = await client.get(CHECK_URL)
            response.raise_for_status()
            data = response.json()
        spent = asyncio.get_running_loop().time() - started
        return {"proxy": label, "ok": True, "ip": str(data.get("ip", "")),
                "ms": int(spent * 1000)}
    except Exception as error:
        return {"proxy": label, "ok": False, "error": f"{type(error).__name__}: {error}"}


@router.post("/api/proxies/check")
async def api_check_proxies(payload: dict[str, Any]) -> dict[str, Any]:
    """Проверить прокси наружу: адрес отвечает и каким IP видно сайт."""
    text = payload.get("text")
    if isinstance(text, str) and text.strip():
        proxies, _ = parse_lines(text)
    else:
        proxies = load_list(PROXIES_FILE)
    if not proxies:
        raise HTTPException(400, "Список прокси пуст")
    try:
        timeout = max(float(payload.get("timeout", 10)), 1.0)
    except (TypeError, ValueError):
        timeout = 10.0
    proxies = proxies[:CHECK_LIMIT]
    semaphore = asyncio.Semaphore(5)

    async def guarded(proxy: str) -> dict[str, Any]:
        async with semaphore:
            return await _check_one(proxy, timeout)

    results = await asyncio.gather(*(guarded(proxy) for proxy in proxies))
    return {"checked": len(results), "ok": sum(1 for item in results if item["ok"]),
            "results": list(results)}
