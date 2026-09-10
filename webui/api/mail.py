"""API почтовых сервисов: список, свои сервисы и их шаги."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from universal import mail_services as store

router = APIRouter(tags=["mail"])


def _load(name: str) -> tuple[Any, dict[str, Any]]:
    try:
        return store.find(name)
    except KeyError:
        if store.normalize(name) in store.BUILTIN:
            raise HTTPException(400, f"{name} — встроенный сервис, его шаги менять не нужно")
        raise HTTPException(404, f"Почтовый сервис не найден: {name}")
    except (OSError, ValueError) as error:
        raise HTTPException(500, str(error))


@router.get("/api/mail-services")
def api_mail_services() -> list[dict[str, str]]:
    """Выбор почты при запуске: «по сценарию» плюс все известные сервисы."""
    items = [{"id": "auto", "label": "По сценарию (авто)", "kind": "auto"}]
    for service in store.summaries():
        items.append({"id": service["id"], "label": service["label"],
                      "kind": service.get("kind", "custom")})
    return items


@router.get("/api/mail/services")
def api_services() -> dict[str, Any]:
    return {"services": store.summaries(),
            "address_sources": list(store.ADDRESS_SOURCES),
            "sections": list(store.STEP_SECTIONS)}


@router.get("/api/mail/templates")
def api_templates() -> list[dict[str, str]]:
    return [{"id": key, "label": value["label"]} for key, value in store.TEMPLATES.items()]


@router.post("/api/mail/services")
def api_create(payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name", "")).strip()
    if not name:
        raise HTTPException(400, "Нужно имя почтового сервиса")
    try:
        path, data = store.create(name, str(payload.get("url", "")).strip(),
                                  str(payload.get("template", "blank") or "blank"),
                                  copy_from=str(payload.get("copy_from") or "") or None,
                                  label=str(payload.get("label", "")))
    except FileExistsError as error:
        raise HTTPException(409, str(error))
    except KeyError:
        raise HTTPException(404, f"Сервис-источник не найден: {payload.get('copy_from')}")
    except (OSError, ValueError) as error:
        raise HTTPException(400, str(error))
    return {"name": data["name"], "file": path.name, "service": data}


@router.get("/api/mail/services/{name}")
def api_get(name: str) -> dict[str, Any]:
    _, data = _load(name)
    return {key: value for key, value in data.items() if not key.startswith("_")}


@router.put("/api/mail/services/{name}")
def api_put(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    path, _ = _load(name)
    errors = store.validate(payload)
    if errors:
        raise HTTPException(400, "; ".join(errors))
    try:
        store.save(path, payload)
    except OSError as error:
        raise HTTPException(500, str(error))
    return {"ok": True, "file": path.name, "name": str(payload.get("name"))}


@router.delete("/api/mail/services/{name}")
def api_delete(name: str) -> dict[str, Any]:
    _load(name)
    try:
        target = store.delete(name)
    except OSError as error:
        raise HTTPException(500, str(error))
    return {"ok": True, "trash": str(target.relative_to(store.ROOT))}


@router.post("/api/mail/services/validate")
def api_validate(payload: dict[str, Any]) -> dict[str, Any]:
    return {"errors": store.validate(payload)}
