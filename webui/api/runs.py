"""API запусков: старт регистрации в фоновом потоке, лог, капча и остановка.

Запуск живёт в отдельном потоке, а панель опрашивает его состояние. Кроме
лога наружу выдаётся то, из-за чего человеку нужно вернуться к экрану:
текущая капча, уведомления шагов и состояние очереди прокси.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException

from core.adspower import AdsPowerProfiles
from core.browser_profile import BrowserProfiles
from core.proxy_pool import (ROTATIONS, ProxyRotation, load_list, normalize_proxy,
                             parse_lines, redact_proxy)
from universal.captcha import CaptchaSettings, CaptchaSkipped, CaptchaStop
from universal.control import CAPTCHA_ANSWERS, RunControl
from universal.mail import mail_choices, normalize_mail_service
from universal.pacing import Pacing
from universal.runner import register_one
from webui import config
from webui.api.scenarios import load as load_scenario
from webui.settings import ADSPOWER_CONFIG, OUTPUT_FILE, PROFILES_DIR, PROXIES_FILE

router = APIRouter(tags=["runs"])

PROXY_MODES = ("direct", "file", "list", "single")
MAX_NOTICES = 30

_jobs: dict[str, dict[str, Any]] = {}
_controls: dict[str, RunControl] = {}
_jobs_lock = threading.Lock()


def _profiles(browser: str, emit) -> tuple[Any, str]:
    config_data: dict[str, Any] = {}
    if ADSPOWER_CONFIG.exists():
        try:
            config_data = json.loads(ADSPOWER_CONFIG.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as error:
            emit(f"[webui] adspower.local.json не прочитан ({error}); беру Chrome")
    use_adspower = browser == "adspower" or (browser == "auto" and bool(config_data))
    if use_adspower and not config_data.get("api_key"):
        emit("[webui] В adspower.local.json нет api_key — беру Chrome")
        use_adspower = False
    if use_adspower:
        return AdsPowerProfiles(PROFILES_DIR,
                                config_data.get("base_url", "http://127.0.0.1:50325"),
                                config_data.get("api_key", "")), "adspower"
    return BrowserProfiles(PROFILES_DIR), "chrome"


def _proxies_for(job: dict[str, Any], emit) -> list[str]:
    """Прокси запуска: файл, список из панели, одна строка или ничего."""
    mode = job["proxy_mode"]
    if mode == "direct":
        return []
    if mode == "single":
        return [job["proxy"]] if job.get("proxy") else []
    if mode == "list":
        proxies, errors = parse_lines(job.get("proxies_text", ""))
        for error in errors:
            emit(f"[webui] Прокси пропущен: {error}")
        return proxies
    proxies = load_list(PROXIES_FILE)
    if not proxies:
        emit(f"[webui] {PROXIES_FILE.name} пуст — иду напрямую")
    return proxies


def _sleep_between(job_id: str, seconds: float, emit) -> None:
    """Пауза между аккаунтами, прерываемая кнопкой «Остановить»."""
    if seconds <= 0:
        return
    emit(f"[webui] Пауза между аккаунтами: {seconds:.0f} с")
    with _jobs_lock:
        _jobs[job_id]["pause_left"] = int(seconds)
    finish = time.monotonic() + seconds
    while time.monotonic() < finish:
        with _jobs_lock:
            if _jobs[job_id].get("stop"):
                break
            _jobs[job_id]["pause_left"] = int(max(0, finish - time.monotonic()))
        time.sleep(0.5)
    with _jobs_lock:
        _jobs[job_id]["pause_left"] = 0


def _listener(job_id: str):
    """События запуска → состояние задания, которое читает панель."""

    def handle(event: dict[str, Any]) -> None:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job is None:
                return
            job["event_seq"] += 1
            event = dict(event, seq=job["event_seq"])
            if event["type"] == "captcha":
                job["captcha"] = event
            elif event["type"] == "captcha_done":
                job["captcha"] = None
            if event["type"] in ("notice", "captcha"):
                job["notices"].append(event)
                del job["notices"][:-MAX_NOTICES]

    return handle


def _run_job(job_id: str) -> None:
    with _jobs_lock:
        job = _jobs[job_id]

    def emit(message: str) -> None:
        job["logs"].append(message)

    control = RunControl(emit, _listener(job_id))
    with _jobs_lock:
        _controls[job_id] = control
    try:
        _, scenario = load_scenario(job["scenario"])
        pacing = Pacing.from_dict(job["delays"])
        captcha = CaptchaSettings.from_dict(job["captcha_settings"])
        rotation = ProxyRotation(_proxies_for(job, emit), job["rotation"], job["ban_after"])
        profiles, backend = _profiles(job["browser"], emit)
        job["proxies"] = rotation.describe()
        emit(f"[webui] Старт: {job['scenario']} x{job['count']}, почта={job['mail']}, "
             f"сеть={rotation.summary()}, браузер={backend}, "
             f"капча={captcha.mode}, пауза шага {int(pacing.step_min_ms)}–"
             f"{int(pacing.step_max_ms)} мс")
        for index in range(job["count"]):
            with _jobs_lock:
                if _jobs[job_id].get("stop"):
                    _jobs[job_id]["status"] = "stopped"
                    emit("[webui] Остановлено пользователем (после текущего аккаунта)")
                    break
            if index:
                _sleep_between(job_id, pacing.account_seconds(), emit)
                with _jobs_lock:
                    if _jobs[job_id].get("stop"):
                        _jobs[job_id]["status"] = "stopped"
                        emit("[webui] Остановлено пользователем")
                        break
            proxy = rotation.take()
            job["current"] = index + 1
            emit(f"[universal] Аккаунт {index + 1}/{job['count']}"
                 f"; сеть={redact_proxy(proxy) if proxy else 'напрямую'}")
            try:
                asyncio.run(register_one(scenario, job["mail"], profiles, OUTPUT_FILE,
                                         proxy, emit, control, pacing, captcha))
                job["done"] += 1
                rotation.report(proxy, True)
            except CaptchaStop:
                rotation.report(proxy, False)
                with _jobs_lock:
                    _jobs[job_id]["status"] = "stopped"
                    _jobs[job_id]["stop"] = True
                emit("[webui] Остановлено на капче по вашей команде")
                break
            except CaptchaSkipped as error:
                job["skipped"] += 1
                rotation.report(proxy, False)
                emit(f"[universal] Аккаунт {index + 1} пропущен: {error}")
            except Exception as error:  # аккаунт упал — очередь продолжается
                job["failed"] += 1
                rotation.report(proxy, False)
                emit(f"[universal] Аккаунт {index + 1} не удался: "
                     f"{type(error).__name__}: {error}")
            job["proxies"] = rotation.describe()
        with _jobs_lock:
            if _jobs[job_id]["status"] == "running":
                _jobs[job_id]["status"] = "done" if not (job["failed"] or job["skipped"]) \
                    else "done_with_errors"
        emit(f"[webui] Готово: ок={job['done']} неудач={job['failed']} "
             f"пропущено={job['skipped']}")
    except Exception as error:
        with _jobs_lock:
            _jobs[job_id]["status"] = "failed"
        emit(f"[webui] Ошибка запуска: {type(error).__name__}: {error}")
    finally:
        with _jobs_lock:
            _jobs[job_id]["captcha"] = None
            _controls.pop(job_id, None)


def _public(job: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in job.items()
            if key not in ("logs", "proxies_text", "proxy")}


@router.post("/api/runs")
def api_start_run(payload: dict[str, Any]) -> dict[str, Any]:
    scenario = str(payload.get("scenario", ""))
    try:
        count = int(payload.get("count", 1))
    except (TypeError, ValueError):
        raise HTTPException(400, "count должен быть числом")
    mail = str(payload.get("mail", "auto"))
    proxy_mode = str(payload.get("proxy_mode", "direct"))
    browser = str(payload.get("browser", "auto"))
    saved = config.load()
    rotation = str(payload.get("rotation") or saved["proxy"]["mode"])
    if not scenario:
        raise HTTPException(400, "Не выбран сценарий")
    if not 1 <= count <= 1000:
        raise HTTPException(400, "count: 1..1000")
    if mail not in ("auto",) + mail_choices():
        raise HTTPException(400, "mail: auto | " + " | ".join(mail_choices()))
    if proxy_mode not in PROXY_MODES:
        raise HTTPException(400, "proxy_mode: " + "|".join(PROXY_MODES))
    if browser not in ("auto", "chrome", "adspower"):
        raise HTTPException(400, "browser: auto|chrome|adspower")
    if rotation not in ROTATIONS:
        raise HTTPException(400, "rotation: " + "|".join(ROTATIONS))
    proxy = str(payload.get("proxy") or "").strip()
    if proxy_mode == "single":
        if not proxy:
            raise HTTPException(400, "Укажите адрес прокси")
        try:
            proxy = normalize_proxy(proxy)
        except ValueError as error:
            raise HTTPException(400, str(error))
    proxies_text = str(payload.get("proxies_text") or "")
    if proxy_mode == "list":
        parsed, errors = parse_lines(proxies_text)
        if not parsed:
            raise HTTPException(400, "Список прокси пуст" + (f": {errors[0]}" if errors else ""))
    _, data = load_scenario(scenario)
    if mail == "auto":
        mail = normalize_mail_service(str(data.get("mail", "tmail"))) or "tmail"
        if mail not in mail_choices():
            mail = "tmail"
    else:
        mail = normalize_mail_service(mail)
    delays = payload.get("delays") if isinstance(payload.get("delays"), dict) else saved["delays"]
    captcha = payload.get("captcha") if isinstance(payload.get("captcha"), dict) \
        else saved["captcha"]
    try:
        ban_after = max(int(payload.get("ban_after", saved["proxy"]["ban_after"])), 0)
    except (TypeError, ValueError):
        ban_after = saved["proxy"]["ban_after"]
    job_id = uuid.uuid4().hex[:8]
    with _jobs_lock:
        finished = [key for key, job in _jobs.items() if job["status"] != "running"]
        for old_id in finished[: max(0, len(_jobs) - 19)]:
            _jobs.pop(old_id, None)
        _jobs[job_id] = {"id": job_id, "scenario": scenario, "count": count, "mail": mail,
                         "proxy_mode": proxy_mode, "rotation": rotation,
                         "ban_after": ban_after, "browser": browser, "status": "running",
                         "done": 0, "failed": 0, "skipped": 0, "current": 0,
                         "logs": [], "stop": False, "captcha": None, "notices": [],
                         "event_seq": 0, "pause_left": 0, "proxies": [],
                         "delays": delays, "captcha_settings": captcha,
                         "proxy": proxy, "proxies_text": proxies_text,
                         "started_at": time.time()}
    threading.Thread(target=_run_job, args=(job_id,), daemon=True,
                     name=f"webui-run-{job_id}").start()
    return {"id": job_id}


@router.get("/api/runs")
def api_runs() -> list[dict[str, Any]]:
    with _jobs_lock:
        return [_public(job) for job in _jobs.values()]


@router.get("/api/runs/{job_id}")
def api_run(job_id: str, offset: int = 0) -> dict[str, Any]:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Запуск не найден")
        return dict(_public(job), logs=job["logs"][max(0, offset):],
                    next_offset=len(job["logs"]))


@router.post("/api/runs/{job_id}/stop")
def api_stop_run(job_id: str) -> dict[str, Any]:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Запуск не найден")
        job["stop"] = True
        control = _controls.get(job_id)
    if control is not None:
        control.request_stop()
    return {"ok": True}


@router.post("/api/runs/{job_id}/captcha")
def api_answer_captcha(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Ответ человека на капчу: решил / пропустить аккаунт / остановить."""
    action = str(payload.get("action") or "resolved")
    if action not in CAPTCHA_ANSWERS:
        raise HTTPException(400, "action: " + " | ".join(CAPTCHA_ANSWERS))
    with _jobs_lock:
        if job_id not in _jobs:
            raise HTTPException(404, "Запуск не найден")
        control = _controls.get(job_id)
        if action == "stop":
            _jobs[job_id]["stop"] = True
    if control is None:
        raise HTTPException(409, "Запуск уже завершён")
    control.answer_captcha(action)
    return {"ok": True, "action": action}
