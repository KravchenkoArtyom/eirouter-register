"""API инспектора: зеркало страницы, выбор элементов и временный буфер."""
from __future__ import annotations

import base64
import threading
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from webui.element_buffer import ElementBuffer
from webui.inspect_session import InspectSession, inject_picker
from webui.settings import BUFFER_FILE, MAX_INSPECT_SESSIONS, STATIC_DIR

router = APIRouter(tags=["inspector"])

buffer = ElementBuffer(BUFFER_FILE)
_sessions: dict[str, InspectSession] = {}
_sessions_lock = threading.Lock()
PICKER_FILE = STATIC_DIR / "picker.js"


def _session(sid: str) -> InspectSession:
    session = _sessions.get(sid)
    if session is None:
        raise HTTPException(404, "Сессия инспектора не найдена")
    return session


# ---- сессия ----
@router.post("/api/inspect/start")
def api_inspect_start(payload: dict[str, Any]) -> dict[str, Any]:
    url = str(payload.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "Нужен http(s) адрес")
    with _sessions_lock:
        while len(_sessions) >= MAX_INSPECT_SESSIONS:
            old_id, old = next(iter(_sessions.items()))
            try:
                old.close()
            except Exception:
                pass
            _sessions.pop(old_id, None)
        sid = uuid.uuid4().hex[:8]
        try:
            session = InspectSession(url)
        except Exception as error:
            raise HTTPException(500, f"Инспектор не открылся: {error}")
        _sessions[sid] = session
    return {"sid": sid, "state": session.state()}


@router.get("/api/inspect/{sid}/state")
def api_inspect_state(sid: str) -> dict[str, Any]:
    try:
        return _session(sid).state()
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(500, f"Состояние не получено: {error}")


@router.get("/api/inspect/{sid}/mirror", response_class=HTMLResponse)
def api_inspect_mirror(sid: str) -> HTMLResponse:
    """Зеркало страницы: тот же DOM, но с нашего origin и без чужих скриптов."""
    session = _session(sid)
    try:
        html = session.mirror()
    except Exception as error:
        raise HTTPException(500, f"Зеркало не собралось: {error}")
    script = PICKER_FILE.read_text(encoding="utf-8")
    return HTMLResponse(inject_picker(html or "", script, sid, session.revision),
                        headers={"Cache-Control": "no-store"})


@router.get("/api/inspect/{sid}/shot")
def api_inspect_shot(sid: str, selector: str = "") -> dict[str, Any]:
    session = _session(sid)
    try:
        png = session.screenshot(selector or None)
    except Exception as error:
        raise HTTPException(500, f"Скриншот не удался: {error}")
    return {"png": base64.b64encode(png).decode(),
            "w": InspectSession.VIEWPORT["width"], "h": InspectSession.VIEWPORT["height"]}


@router.post("/api/inspect/{sid}/pick")
def api_inspect_pick(sid: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Резервный режим: клик по скриншоту -> элемент под точкой."""
    session = _session(sid)
    try:
        width = max(1.0, float(payload.get("dw", 1)))
        height = max(1.0, float(payload.get("dh", 1)))
        x = float(payload.get("x", 0)) * InspectSession.VIEWPORT["width"] / width
        y = float(payload.get("y", 0)) * InspectSession.VIEWPORT["height"] / height
    except (TypeError, ValueError):
        raise HTTPException(400, "Координаты должны быть числами")
    try:
        return {"element": session.pick(x, y)}
    except Exception as error:
        raise HTTPException(500, f"Элемент не распознан: {error}")


@router.post("/api/inspect/{sid}/verify")
def api_inspect_verify(sid: str, payload: dict[str, Any]) -> dict[str, Any]:
    session = _session(sid)
    selector = str(payload.get("selector", "")).strip()
    if not selector:
        raise HTTPException(400, "Пустой селектор")
    try:
        return session.verify(selector)
    except Exception as error:
        raise HTTPException(400, f"Селектор не проверить: {error}")


@router.post("/api/inspect/{sid}/act")
def api_inspect_act(sid: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Выполнить действие на настоящей странице и обновить зеркало."""
    session = _session(sid)
    action = str(payload.get("action", "")).strip()
    allowed = ("click", "fill", "type", "check", "uncheck", "press", "select",
               "scroll", "goto", "reload", "back")
    if action not in allowed:
        raise HTTPException(400, "action: " + " | ".join(allowed))
    selector = str(payload.get("selector", "")).strip()
    if action in ("click", "fill", "type", "check", "uncheck", "press", "select") and not selector:
        raise HTTPException(400, "Для этого действия нужен селектор")
    try:
        return session.act(action, selector, str(payload.get("value", "")))
    except Exception as error:
        raise HTTPException(400, f"Действие не выполнено: {error}")


@router.delete("/api/inspect/{sid}")
def api_inspect_close(sid: str) -> dict[str, Any]:
    with _sessions_lock:
        session = _sessions.pop(sid, None)
    if session is None:
        raise HTTPException(404, "Сессия инспектора не найдена")
    try:
        session.close()
    except Exception:
        pass
    return {"ok": True}


# ---- временный буфер элементов ----
@router.get("/api/buffer")
def api_buffer_list() -> list[dict[str, Any]]:
    return buffer.all()


@router.post("/api/buffer")
def api_buffer_add(payload: dict[str, Any]) -> dict[str, Any]:
    if not str(payload.get("selector", "")).strip():
        raise HTTPException(400, "Нужен селектор")
    return buffer.add(payload)


@router.patch("/api/buffer/{item_id}")
def api_buffer_update(item_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    item = buffer.update(item_id, payload)
    if item is None:
        raise HTTPException(404, "Элемент буфера не найден")
    return item


@router.delete("/api/buffer/{item_id}")
def api_buffer_remove(item_id: str) -> dict[str, Any]:
    if not buffer.remove(item_id):
        raise HTTPException(404, "Элемент буфера не найден")
    return {"ok": True}


@router.delete("/api/buffer")
def api_buffer_clear() -> dict[str, Any]:
    return {"ok": True, "removed": buffer.clear()}
