"""API аккаунтов и состояния окружения.

Пароли и ключи наружу не отдаются: панель показывает только почту, сценарий,
статус и признак «ключ есть». Полные данные лежат в файлах проекта.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from core.proxy_pool import load_list
from universal import mail_services
from webui.settings import ACCOUNT_FILES, ADSPOWER_CONFIG, PROXIES_FILE, ROOT

router = APIRouter(tags=["accounts"])

CSV_COLUMNS = ("file", "email", "provider", "status", "has_key", "created_at")


def _safe_records(filename: str) -> list[dict[str, Any]]:
    path = ROOT / filename
    if not path.exists():
        return []
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        records = []
    if not isinstance(records, list):
        records = []
    return [{"email": record.get("email", ""), "provider": record.get("provider", ""),
             "status": record.get("status", ""), "has_key": bool(record.get("api_key")),
             "created_at": record.get("created_at", "")}
            for record in records if isinstance(record, dict)]


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
        if not (ROOT / filename).exists():
            continue
        safe = _safe_records(filename)
        result["files"].append({"file": filename, "total": len(safe), "accounts": safe})
    return result


@router.get("/api/accounts/export.csv")
def api_accounts_csv() -> StreamingResponse:
    """Тот же список одной таблицей — открыть в Excel или залить в базу."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for filename in ACCOUNT_FILES:
        for record in _safe_records(filename):
            writer.writerow(dict(record, file=filename,
                                 has_key="да" if record["has_key"] else ""))
    body = "\ufeff" + buffer.getvalue()  # BOM — чтобы Excel увидел UTF-8
    return StreamingResponse(iter([body]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition":
                                      'attachment; filename="accounts.csv"'})
