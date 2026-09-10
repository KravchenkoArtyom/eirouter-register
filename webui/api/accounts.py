"""API аккаунтов и состояния окружения."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter

from core.proxy_pool import load_list
from universal import mail_services
from webui.settings import ACCOUNT_FILES, ADSPOWER_CONFIG, PROXIES_FILE, ROOT

router = APIRouter(tags=["accounts"])


@router.get("/api/health")
def api_health() -> dict[str, Any]:
    return {"ok": True}


@router.get("/api/environment")
def api_environment() -> dict[str, Any]:
    """Что доступно на этой машине: AdsPower, прокси-файл."""
    config: dict[str, Any] = {}
    if ADSPOWER_CONFIG.exists():
        try:
            config = json.loads(ADSPOWER_CONFIG.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            config = {}
    try:
        proxies = len(load_list(PROXIES_FILE)) if PROXIES_FILE.exists() else 0
    except ValueError:  # в файле есть битая строка — точное число покажет /api/proxies
        proxies = 0
    return {"adspower": bool(config.get("api_key")),
            "proxies": proxies,
            "proxies_file": PROXIES_FILE.name,
            "mail_services": len(mail_services.custom_ids())}


@router.get("/api/accounts")
def api_accounts() -> dict[str, Any]:
    """Аккаунты из всех файлов проекта без паролей и ключей."""
    result: dict[str, Any] = {"files": []}
    for filename in ACCOUNT_FILES:
        path = ROOT / filename
        if not path.exists():
            continue
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            records = []
        safe = [{"email": record.get("email", ""), "provider": record.get("provider", ""),
                 "status": record.get("status", ""), "has_key": bool(record.get("api_key")),
                 "created_at": record.get("created_at", "")}
                for record in records if isinstance(record, dict)]
        result["files"].append({"file": filename, "total": len(safe), "accounts": safe})
    return result
