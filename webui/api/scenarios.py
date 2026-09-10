"""API сценариев: список, чтение, создание, изменение, удаление, валидация."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from universal import scenarios as store
from universal.actions import catalog
from universal.mail import mail_choices

router = APIRouter(tags=["scenarios"])


def load(name: str) -> tuple[Any, dict[str, Any]]:
    try:
        return store.find(name)
    except KeyError:
        raise HTTPException(404, f"Сценарий не найден: {name}")
    except (OSError, ValueError) as error:
        raise HTTPException(500, str(error))


@router.get("/api/actions")
def api_actions() -> dict[str, Any]:
    """Каталог действий: из него WebUI строит форму шага."""
    return catalog()


@router.get("/api/templates")
def api_templates() -> list[dict[str, str]]:
    return [{"id": key, "label": value["label"]} for key, value in store.TEMPLATES.items()]


@router.get("/api/scenarios")
def api_scenarios() -> list[dict[str, Any]]:
    try:
        return store.summaries()
    except (OSError, ValueError) as error:
        raise HTTPException(500, str(error))


@router.post("/api/scenarios")
def api_create_scenario(payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name", "")).strip()
    url = str(payload.get("url", "")).strip()
    mail = str(payload.get("mail", "tmail")).strip() or "tmail"
    template = str(payload.get("template", "blank")).strip() or "blank"
    copy_from = payload.get("copy_from") or None
    if not name:
        raise HTTPException(400, "Нужно имя сценария")
    if mail not in mail_choices():
        raise HTTPException(400, "mail: " + " | ".join(mail_choices()))
    try:
        path, data = store.create(name, url, mail, template,
                                  copy_from=str(copy_from) if copy_from else None)
    except FileExistsError as error:
        raise HTTPException(409, str(error))
    except KeyError:
        raise HTTPException(404, f"Сценарий-источник не найден: {copy_from}")
    except (OSError, ValueError) as error:
        raise HTTPException(400, str(error))
    return {"name": store.title_of(path, data), "file": path.name, "scenario": data}


@router.get("/api/scenarios/{name}")
def api_get_scenario(name: str) -> dict[str, Any]:
    _, data = load(name)
    return data


@router.put("/api/scenarios/{name}")
def api_put_scenario(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    errors = store.validate(payload)
    if errors:
        raise HTTPException(400, "; ".join(errors))
    path, _ = load(name)
    try:
        store.save(path, payload)
    except OSError as error:
        raise HTTPException(500, str(error))
    return {"ok": True, "file": path.name, "name": store.title_of(path, payload)}


@router.delete("/api/scenarios/{name}")
def api_delete_scenario(name: str) -> dict[str, Any]:
    load(name)
    try:
        target = store.delete(name)
    except OSError as error:
        raise HTTPException(500, str(error))
    return {"ok": True, "trash": str(target.relative_to(store.ROOT))}


@router.post("/api/scenarios/validate")
def api_validate(payload: dict[str, Any]) -> dict[str, Any]:
    return {"errors": store.validate(payload)}
