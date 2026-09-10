"""API запусков: старт регистрации в фоновом потоке, лог, капча и остановка.

Запуск живёт в отдельном потоке (или в нескольких, если выбрано больше одного
аккаунта одновременно), а панель читает его состояние: опросом
`GET /api/runs/{id}` или потоком событий `GET /api/runs/{id}/stream`. Кроме
лога наружу выдаётся то, из-за чего человеку нужно вернуться к экрану: текущая
капча, уведомления шагов и состояние очереди прокси.

Полный лог и итог каждого запуска остаются на диске (`logs/runs/`), поэтому
после перезапуска панели видно, что происходило; снимки упавших аккаунтов —
в `logs/failures/`.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from core.adspower import AdsPowerProfiles
from core.browser_profile import BrowserProfiles
from core.proxy_pool import (ROTATIONS, ProxyRotation, load_list, normalize_proxy,
                             parse_lines, redact_proxy)
from core.run_logs import mask_secrets
from core.storage import AccountStore
from universal.captcha import CaptchaSettings, CaptchaSkipped, CaptchaStop
from universal.control import CAPTCHA_ANSWERS, RunControl
from universal.mail import mail_choices, normalize_mail_service
from universal.pacing import Pacing
from universal.runner import register_one
from webui import config
from webui.api.scenarios import load as load_scenario
from webui.settings import (ADSPOWER_CONFIG, FAILURES_DIR, MAX_RUN_WORKERS, OUTPUT_FILE,
                            PROFILES_DIR, PROXIES_FILE, RUNS_LOG_DIR, RUN_LOG_LIMIT)

router = APIRouter(tags=["runs"])

PROXY_MODES = ("direct", "file", "list", "single")
MAX_NOTICES = 30
MAX_JOBS = 20

_jobs: dict[str, dict[str, Any]] = {}
_controls: dict[str, RunControl] = {}
_stores: dict[str, AccountStore] = {}
_jobs_lock = threading.Lock()


def shutdown() -> None:
    """Остановить запуски и закрыть хранилища (вызывается при выходе)."""
    with _jobs_lock:
        jobs = list(_jobs.values())
        controls = list(_controls.values())
        stores = list(_stores.values())
        _stores.clear()
    for job in jobs:
        job["stop"] = True
    for control in controls:
        control.request_stop()
    for store in stores:
        store.close()


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


# ---- лог запуска ----
class RunLog:
    """Строки лога: в памяти — последние, на диске — все.

    `offset` панели считается от начала запуска, поэтому обрезка старых строк
    учитывается через `dropped`.
    """

    def __init__(self, path) -> None:
        self.path = path
        self.lines: list[str] = []
        self.dropped = 0
        self._lock = threading.Lock()
        self._file = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file = path.open("a", encoding="utf-8")
        except OSError:  # без файла работаем только в памяти
            self._file = None

    def add(self, message: str) -> None:
        line = mask_secrets(str(message))
        with self._lock:
            self.lines.append(line)
            extra = len(self.lines) - RUN_LOG_LIMIT
            if extra > 0:
                del self.lines[:extra]
                self.dropped += extra
            if self._file is not None:
                stamp = time.strftime("%H:%M:%S")
                try:
                    self._file.write(f"[{stamp}] {line}\n")
                    self._file.flush()
                except OSError:
                    self._file = None

    def since(self, offset: int) -> tuple[list[str], int]:
        with self._lock:
            start = max(0, int(offset) - self.dropped)
            return list(self.lines[start:]), self.dropped + len(self.lines)

    def close(self) -> None:
        with self._lock:
            if self._file is not None:
                try:
                    self._file.close()
                finally:
                    self._file = None


def _journal(job: dict[str, Any]) -> None:
    """Итог запуска рядом с логом — чтобы история жила и после перезапуска."""
    path = job.get("journal")
    if not path:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_public(job), ensure_ascii=False, indent=2, default=str)
                        + "\n", encoding="utf-8")
    except OSError:
        pass


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


def _register(job: dict[str, Any], scenario: dict[str, Any], profiles, store: AccountStore,
              control: RunControl, pacing: Pacing, captcha: CaptchaSettings,
              proxy: str | None, emit) -> Any:
    """Один аккаунт. Отдельная функция — её подменяют тесты."""
    return asyncio.run(register_one(scenario, job["mail"], profiles, OUTPUT_FILE, proxy,
                                    emit, control, pacing, captcha, store=store,
                                    artifacts=FAILURES_DIR / job["id"]))


def _account(job: dict[str, Any], index: int, scenario: dict[str, Any], profiles,
             store: AccountStore, control: RunControl, pacing: Pacing,
             captcha: CaptchaSettings, rotation: ProxyRotation, emit,
             counters: threading.Lock) -> None:
    """Зарегистрировать один аккаунт и разложить исход по счётчикам задания."""
    proxy = rotation.take()
    with counters:
        job["current"] = max(job["current"], index + 1)
        job["active"] += 1
    emit(f"[universal] Аккаунт {index + 1}/{job['count']}"
         f"; сеть={redact_proxy(proxy) if proxy else 'напрямую'}")
    try:
        _register(job, scenario, profiles, store, control, pacing, captcha, proxy, emit)
        with counters:
            job["done"] += 1
        rotation.report(proxy, True)
    except CaptchaStop:
        rotation.report(proxy, False)
        with _jobs_lock:
            job["status"] = "stopped"
            job["stop"] = True
        emit("[webui] Остановлено на капче по вашей команде")
    except CaptchaSkipped as error:
        with counters:
            job["skipped"] += 1
        rotation.report(proxy, False)
        emit(f"[universal] Аккаунт {index + 1} пропущен: {error}")
    except Exception as error:  # аккаунт упал — очередь продолжается
        with counters:
            job["failed"] += 1
        rotation.report(proxy, False)
        emit(f"[universal] Аккаунт {index + 1} не удался: "
             f"{type(error).__name__}: {error}")
    finally:
        with counters:
            job["active"] -= 1
        job["proxies"] = rotation.describe()


def _wait_slot(slots: threading.Semaphore, job: dict[str, Any]) -> bool:
    """Дождаться свободного слота под аккаунт. False — попросили остановиться."""
    while not job.get("stop"):
        if slots.acquire(timeout=0.5):
            return True
    return False


def _mark_stopped(job: dict[str, Any], emit, message: str) -> None:
    with _jobs_lock:
        if job["status"] == "running":
            job["status"] = "stopped"
    emit(message)


def _run_job(job_id: str) -> None:
    with _jobs_lock:
        job = _jobs[job_id]
    log: RunLog = job["log"]

    def emit(message: str) -> None:
        log.add(message)

    control = RunControl(emit, _listener(job_id))
    store = AccountStore(OUTPUT_FILE)
    with _jobs_lock:
        _controls[job_id] = control
        _stores[job_id] = store
    counters = threading.Lock()
    threads: list[threading.Thread] = []
    try:
        _, scenario = load_scenario(job["scenario"])
        pacing = Pacing.from_dict(job["delays"])
        captcha = CaptchaSettings.from_dict(job["captcha_settings"])
        rotation = ProxyRotation(_proxies_for(job, emit), job["rotation"], job["ban_after"])
        profiles, backend = _profiles(job["browser"], emit)
        job["proxies"] = rotation.describe()
        workers = job["workers"]
        emit(f"[webui] Старт: {job['scenario']} x{job['count']}, почта={job['mail']}, "
             f"сеть={rotation.summary()}, браузер={backend}, "
             f"капча={captcha.mode}, потоков={workers}, пауза шага "
             f"{int(pacing.step_min_ms)}–{int(pacing.step_max_ms)} мс")
        slots = threading.Semaphore(workers)
        for index in range(job["count"]):
            if not _wait_slot(slots, job):
                _mark_stopped(job, emit,
                              "[webui] Остановлено пользователем (после текущего аккаунта)")
                break
            if index:
                _sleep_between(job_id, pacing.account_seconds(), emit)
                if job.get("stop"):
                    slots.release()
                    _mark_stopped(job, emit, "[webui] Остановлено пользователем")
                    break

            def task(number: int = index) -> None:
                try:
                    _account(job, number, scenario, profiles, store, control, pacing,
                             captcha, rotation, emit, counters)
                finally:
                    slots.release()
                    _journal(job)

            if workers == 1:
                task()
            else:
                thread = threading.Thread(target=task, daemon=True,
                                          name=f"webui-account-{job_id}-{index + 1}")
                thread.start()
                threads.append(thread)
        for thread in threads:
            thread.join()
        with _jobs_lock:
            if job["status"] == "running":
                job["status"] = "done" if not (job["failed"] or job["skipped"]) \
                    else "done_with_errors"
        emit(f"[webui] Готово: ок={job['done']} неудач={job['failed']} "
             f"пропущено={job['skipped']}")
    except Exception as error:
        with _jobs_lock:
            job["status"] = "failed"
        emit(f"[webui] Ошибка запуска: {type(error).__name__}: {error}")
    finally:
        with _jobs_lock:
            job["captcha"] = None
            job["finished_at"] = time.time()
            _controls.pop(job_id, None)
            _stores.pop(job_id, None)
        store.close()
        _journal(job)
        log.close()


def _public(job: dict[str, Any]) -> dict[str, Any]:
    hidden = ("logs", "log", "proxies_text", "proxy", "journal")
    result = {key: value for key, value in job.items() if key not in hidden}
    log: RunLog | None = job.get("log")
    if log is not None:
        result["log_file"] = log.path.name
    return result


def _snapshot(job: dict[str, Any], offset: int) -> dict[str, Any]:
    lines, next_offset = job["log"].since(offset)
    return dict(_public(job), logs=lines, next_offset=next_offset,
                dropped=job["log"].dropped)


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
    try:
        workers = int(payload.get("workers", 1) or 1)
    except (TypeError, ValueError):
        raise HTTPException(400, "workers должен быть числом")
    if not scenario:
        raise HTTPException(400, "Не выбран сценарий")
    if not 1 <= count <= 1000:
        raise HTTPException(400, "count: 1..1000")
    if not 1 <= workers <= MAX_RUN_WORKERS:
        raise HTTPException(400, f"workers: 1..{MAX_RUN_WORKERS}")
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
        if any(item["status"] == "running" for item in _jobs.values()):
            raise HTTPException(409, "Запуск уже идёт: дождитесь конца или остановите его")
        finished = [key for key, item in _jobs.items() if item["status"] != "running"]
        for old_id in finished[: max(0, len(_jobs) + 1 - MAX_JOBS)]:
            _jobs.pop(old_id, None)
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        _jobs[job_id] = {"id": job_id, "scenario": scenario, "count": count, "mail": mail,
                         "proxy_mode": proxy_mode, "rotation": rotation,
                         "ban_after": ban_after, "browser": browser, "status": "running",
                         "done": 0, "failed": 0, "skipped": 0, "current": 0, "active": 0,
                         "workers": workers, "stop": False, "captcha": None, "notices": [],
                         "event_seq": 0, "pause_left": 0, "proxies": [],
                         "delays": delays, "captcha_settings": captcha,
                         "proxy": proxy, "proxies_text": proxies_text,
                         "started_at": time.time(), "finished_at": None,
                         "log": RunLog(RUNS_LOG_DIR / f"run_{stamp}_{job_id}.log"),
                         "journal": RUNS_LOG_DIR / f"run_{stamp}_{job_id}.json"}
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
        return _snapshot(job, offset)


@router.get("/api/runs/{job_id}/stream")
async def api_run_stream(job_id: str, offset: int = 0) -> StreamingResponse:
    """Поток состояния запуска (SSE): панель не опрашивает сервер по таймеру."""
    with _jobs_lock:
        if job_id not in _jobs:
            raise HTTPException(404, "Запуск не найден")

    async def events() -> Any:
        position = offset
        last = ""
        while True:
            with _jobs_lock:
                job = _jobs.get(job_id)
                snapshot = _snapshot(job, position) if job is not None else None
            if snapshot is None:
                break
            position = snapshot["next_offset"]
            body = json.dumps(snapshot, ensure_ascii=False, default=str)
            if snapshot["logs"] or body != last:
                last = body
                yield f"data: {body}\n\n"
            if snapshot["status"] != "running":
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store",
                                      "X-Accel-Buffering": "no"})


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
