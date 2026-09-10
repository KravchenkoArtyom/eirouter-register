"""API настроек панели: задержки, капча, ротация прокси, уведомления."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from webui import config

router = APIRouter(tags=["settings"])


@router.get("/api/settings")
def api_settings() -> dict[str, Any]:
    return {"settings": config.load(), "options": config.options(),
            "defaults": config.DEFAULTS}


@router.put("/api/settings")
def api_save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    patch = {key: value for key, value in payload.items() if key in config.SECTIONS}
    if not patch:
        raise HTTPException(400, "Нечего сохранять: разделы — " + ", ".join(config.SECTIONS))
    try:
        saved = config.save(patch)
    except OSError as error:
        raise HTTPException(500, str(error))
    return {"ok": True, "settings": saved}
