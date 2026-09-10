"""API сценариев: список, чтение, создание, изменение, удаление, валидация.

Отдельно живёт «сухой прогон»: сценарий не выполняется, но каждый селектор
проверяется на настоящей странице сайта — сразу видно, какие шаги придётся
править, не создавая аккаунт.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from universal import scenarios as store
from universal.actions import catalog, needs_selector
from universal.mail import mail_choices
from universal.step_check import selectors_of
from webui.inspect_session import InspectSession

router = APIRouter(tags=["scenarios"])

DRY_RUN_LIMIT = 60


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


def _steps_with_selectors(data: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for section, steps in store.iter_sections(data):
        for number, step in enumerate(steps, 1):
            if not isinstance(step, dict):
                continue
            action = str(step.get("action", ""))
            selectors = selectors_of(step)
            if not selectors or not needs_selector(action):
                continue
            items.append({"where": f"{section}[{number}]", "action": action,
                          "selectors": selectors, "note": str(step.get("note") or "")})
    return items


@router.post("/api/scenarios/{name}/dry-run")
def api_dry_run(name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Открыть страницу сценария и проверить селекторы шагов, ничего не нажимая.

    Проверяется состояние первой страницы: шаги, которые появляются только
    после отправки формы, честно отметятся как ненайденные — это не ошибка
    сценария, а порядок вещей.
    """
    _, data = load(name)
    url = str((payload or {}).get("url") or data.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "У сценария нет адреса страницы")
    steps = _steps_with_selectors(data)[:DRY_RUN_LIMIT]
    result: dict[str, Any] = {"url": url, "errors": store.validate(data),
                              "checked": 0, "problems": 0, "steps": []}
    if not steps:
        return result
    try:
        session = InspectSession(url)
    except Exception as error:
        raise HTTPException(500, f"Страница не открылась: {error}")
    try:
        result["url"] = session.state().get("url", url)
        for step in steps:
            found = ""
            count = 0
            problem = ""
            for selector in step["selectors"]:
                try:
                    check = session.verify(selector)
                except Exception as error:
                    problem = f"{type(error).__name__}: {error}"
                    continue
                if check.get("count"):
                    found, count = selector, int(check["count"])
                    break
            result["checked"] += 1
            if not found:
                result["problems"] += 1
            result["steps"].append({**step, "found": found, "count": count,
                                    "ok": bool(found), "error": problem})
    finally:
        session.close()
    return result
