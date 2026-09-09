"""WebUI для universal_autoregister.py (FastAPI + webui.html).

Запуск:  webui.bat   или   py -3 webui.py [--port 8765]
Адрес:   http://127.0.0.1:8765  (только localhost)
"""
from __future__ import annotations
import argparse
import asyncio
import base64
import json
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
import uvicorn
from universal_autoregister import ROOT, MAIL_CHOICES, load_scenarios, normalize_mail_service, register_one
from core.adspower import AdsPowerProfiles
from core.browser_profile import BrowserProfiles
from core.proxy_pool import load_list
OUTPUT_FILE = ROOT / "universal_accounts.json"
ACCOUNT_FILES = ("universal_accounts.json", "eirouter_accounts.json", "wisdomsatan_accounts.json")
ADSPOWER_CONFIG = ROOT / "adspower.local.json"
PROFILES_DIR = ROOT / "universal_profiles"
PROXIES_FILE = ROOT / "proxies.txt"
app = FastAPI(title="Universal Autoregister")
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
def _scenario_list() -> list[dict[str, Any]]:
    out = []
    for item in load_scenarios():
        path = item["_path"]
        out.append({"name": str(item.get("name", path.stem)), "file": path.name,
                    "url": item.get("url", ""), "steps": len(item.get("steps", [])),
                    "has_verification": bool(item.get("verification"))})
    return out
def _load_scenario_file(name: str) -> tuple[Path, dict[str, Any]]:
    for item in load_scenarios():
        path = item["_path"]
        if str(item.get("name", path.stem)) == name or path.stem == name:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise HTTPException(500, f"Сценарий {name}: корень не объект")
            return path, data
    raise HTTPException(404, f"Сценарий не найден: {name}")
def _validate(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return ["Корень должен быть JSON-объектом"]
    errors = []
    if not isinstance(data.get("url"), str) or not data["url"].strip():
        errors.append("Поле url обязательно")
    if "mail" in data and normalize_mail_service(str(data["mail"])) not in MAIL_CHOICES:
        errors.append("mail: none|tmail|wibucrypto|mail.tm")
    def _check_steps(steps, path):
        if not isinstance(steps, list):
            errors.append(f"{path} должен быть списком")
            return
        for i, step in enumerate(steps):
            if not isinstance(step, dict) or not isinstance(step.get("action"), str):
                errors.append(f"{path}[{i}]: нужен action")
    for section in ("steps", "submit", "success"):
        if section in data:
            _check_steps(data[section], section)
    verification = data.get("verification")
    if verification is not None:
        if not isinstance(verification, dict):
            errors.append("verification должен быть объектом")
        else:
            for sub in ("send", "wait", "complete"):
                if sub in verification:
                    _check_steps(verification[sub], f"verification.{sub}")
    return errors
def _run_job(job_id: str) -> None:
    with _jobs_lock:
        job = _jobs[job_id]
    def emit(message: str) -> None:
        job["logs"].append(message)
    try:
        _, scenario = _load_scenario_file(job["scenario"])
        proxies: list[str] = []
        if job["proxy_mode"] == "file":
            proxies = load_list(PROXIES_FILE)
            if not proxies:
                emit("[webui] proxies.txt пуст — иду напрямую")
        browser = job["browser"]
        config = json.loads(ADSPOWER_CONFIG.read_text(encoding="utf-8")) if ADSPOWER_CONFIG.exists() else {}
        use_adspower = browser == "adspower" or (browser == "auto" and bool(config))
        if use_adspower and not config.get("api_key"):
            emit("[webui] Нет api_key в adspower.local.json — использую Chrome")
            use_adspower = False
        if use_adspower:
            profiles = AdsPowerProfiles(PROFILES_DIR, config.get("base_url", "http://127.0.0.1:50325"), config.get("api_key", ""))
        else:
            profiles = BrowserProfiles(PROFILES_DIR)
        emit(f"[webui] Старт: {job['scenario']} x{job['count']}, почта={job['mail']}, сеть={'прокси-файл' if proxies else 'прямо'}, браузер={'adspower' if use_adspower else 'chrome'}")
        for i in range(job["count"]):
            with _jobs_lock:
                if _jobs[job_id].get("stop"):
                    _jobs[job_id]["status"] = "stopped"
                    emit("[webui] Остановлено пользователем (после текущего аккаунта)")
                    break
            emit(f"[universal] Account {i + 1}/{job['count']}")
            try:
                asyncio.run(register_one(scenario, job["mail"], profiles, OUTPUT_FILE,
                                         proxies[i % len(proxies)] if proxies else None, emit))
                job["done"] += 1
            except Exception as err:
                job["failed"] += 1
                emit(f"[universal] Account {i + 1} failed: {type(err).__name__}: {err}")
        with _jobs_lock:
            if _jobs[job_id]["status"] == "running":
                _jobs[job_id]["status"] = "done" if job["failed"] == 0 else "done_with_errors"
        emit(f"[webui] Готово: ok={job['done']} fail={job['failed']}")
    except Exception as err:
        with _jobs_lock:
            _jobs[job_id]["status"] = "failed"
        emit(f"[webui] Ошибка запуска: {type(err).__name__}: {err}")
@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (ROOT / "webui.html").read_text(encoding="utf-8")
@app.get("/api/health")
def api_health() -> dict[str, Any]:
    return {"ok": True}
@app.get("/api/scenarios")
def api_scenarios() -> list[dict[str, Any]]:
    try:
        return _scenario_list()
    except Exception as err:
        raise HTTPException(500, str(err))
@app.get("/api/scenarios/{name}")
def api_get_scenario(name: str) -> dict[str, Any]:
    _, data = _load_scenario_file(name)
    return data
@app.put("/api/scenarios/{name}")
def api_put_scenario(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    errors = _validate(payload)
    if errors:
        raise HTTPException(400, "; ".join(errors))
    path, _ = _load_scenario_file(name)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "file": path.name}
@app.post("/api/scenarios/validate")
def api_validate(payload: dict[str, Any]) -> dict[str, Any]:
    return {"errors": _validate(payload)}
@app.post("/api/runs")
def api_start_run(payload: dict[str, Any]) -> dict[str, Any]:
    scenario = str(payload.get("scenario", ""))
    try:
        count = int(payload.get("count", 1))
    except (TypeError, ValueError):
        raise HTTPException(400, "count должен быть числом")
    mail = str(payload.get("mail", "tmail"))
    proxy_mode = str(payload.get("proxy_mode", "direct"))
    browser = str(payload.get("browser", "auto"))
    if not scenario:
        raise HTTPException(400, "Не выбран сценарий")
    if not 1 <= count <= 1000:
        raise HTTPException(400, "count: 1..1000")
    if mail not in ("auto", "none", "tmail", "wibucrypto", "mail.tm"):
        raise HTTPException(400, "mail: auto|none|tmail|wibucrypto|mail.tm")
    if proxy_mode not in ("direct", "file"):
        raise HTTPException(400, "proxy_mode: direct|file")
    if browser not in ("auto", "chrome", "adspower"):
        raise HTTPException(400, "browser: auto|chrome|adspower")
    _, sc_data = _load_scenario_file(scenario)
    if mail == "auto":
        mail = normalize_mail_service(str(sc_data.get("mail", "tmail"))) or "tmail"
        if mail not in MAIL_CHOICES:
            mail = "tmail"
    else:
        mail = normalize_mail_service(mail)
    job_id = uuid.uuid4().hex[:8]
    with _jobs_lock:
        done_ids = [jid for jid, j in _jobs.items() if j["status"] != "running"]
        for old_id in done_ids[: max(0, len(_jobs) - 19)]:
            _jobs.pop(old_id, None)
        _jobs[job_id] = {"id": job_id, "scenario": scenario, "count": count, "mail": mail,
                         "proxy_mode": proxy_mode, "browser": browser, "status": "running",
                         "done": 0, "failed": 0, "logs": [], "stop": False}
    threading.Thread(target=_run_job, args=(job_id,), daemon=True, name=f"webui-run-{job_id}").start()
    return {"id": job_id}
@app.get("/api/runs")
def api_runs() -> list[dict[str, Any]]:
    with _jobs_lock:
        return [{k: v for k, v in job.items() if k != "logs"} for job in _jobs.values()]
@app.get("/api/runs/{job_id}")
def api_run(job_id: str, offset: int = 0) -> dict[str, Any]:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Запуск не найден")
        return {"id": job_id, "status": job["status"], "scenario": job["scenario"],
                "count": job["count"], "done": job["done"], "failed": job["failed"],
                "logs": job["logs"][max(0, offset):], "next_offset": len(job["logs"])}
@app.post("/api/runs/{job_id}/stop")
def api_stop_run(job_id: str) -> dict[str, Any]:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Запуск не найден")
        job["stop"] = True
    return {"ok": True}
@app.get("/api/accounts")
def api_accounts() -> dict[str, Any]:
    result: dict[str, Any] = {"files": []}
    for filename in ACCOUNT_FILES:
        path = ROOT / filename
        if not path.exists():
            continue
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            records = []
        safe = [{"email": r.get("email", ""), "provider": r.get("provider", ""),
                 "status": r.get("status", ""), "has_key": bool(r.get("api_key"))}
                for r in records if isinstance(r, dict)]
        result["files"].append({"file": filename, "total": len(safe), "accounts": safe})
    return result
@app.get("/api/mail-services")
def api_mail_services() -> list[dict[str, str]]:
    return [{"id": "auto", "label": "По сценарию (авто)"},
            {"id": "tmail", "label": "tmail (браузер)"},
            {"id": "wibucrypto", "label": "wibucrypto (алиас tmail)"},
            {"id": "mail.tm", "label": "Mail.tm (API)"},
            {"id": "none", "label": "Без почты"}]


@app.get("/api/proxies")
def api_proxies() -> dict[str, Any]:
    if not PROXIES_FILE.exists():
        return {"exists": False, "count": 0}
    return {"exists": True, "count": len(load_list(PROXIES_FILE))}
class InspectSession:
    """Живая страница для подбора селекторов (sync Playwright в своём потоке).

    Фронт показывает скриншот, клик по нему резолвится через elementFromPoint
    в кандидаты селекторов, подсветка проверяет селектор перед сохранением
    в шаг. Селекторы валидны в том же контексте, где потом поедет сценарий.
    """

    VIEWPORT = {"width": 1280, "height": 800}

    def __init__(self, url: str) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._error: Exception | None = None
        threading.Thread(target=self._worker, args=(url,), daemon=True,
                         name="webui-inspect").start()
        if not self._ready.wait(timeout=90):
            raise TimeoutError("браузер инспектора не стартовал")
        if self._error is not None:
            raise self._error

    def _worker(self, url: str) -> None:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                try:
                    browser = pw.chromium.launch(headless=True)
                except Exception:
                    browser = pw.chromium.launch(headless=True, channel="chrome")
                page = browser.new_page(viewport=dict(self.VIEWPORT))
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                self._ready.set()
                while True:
                    fn, box = self._queue.get()
                    if fn is None:
                        break
                    try:
                        box["ret"] = fn(page)
                    except Exception as err:
                        box["err"] = err
                    box["done"].set()
                browser.close()
        except Exception as err:
            self._error = err
            self._ready.set()

    def call(self, fn, timeout: float = 60.0):
        box: dict[str, Any] = {"done": threading.Event()}
        self._queue.put((fn, box))
        if not box["done"].wait(timeout):
            raise TimeoutError("инспектор не ответил")
        if "err" in box:
            raise box["err"]
        return box.get("ret")

    def close(self) -> None:
        self._queue.put((None, None))


PICK_JS = """(pt) => {
  const el = document.elementFromPoint(pt[0], pt[1]);
  if (!el || !(el instanceof Element)) return null;
  const cands = [];
  const push = s => { if (s && !cands.includes(s)) cands.push(s); };
  if (el.id) push('#' + CSS.escape(el.id));
  for (const a of ['data-testid', 'data-test', 'name', 'aria-label', 'placeholder']) {
    const v = el.getAttribute ? el.getAttribute(a) : null;
    if (v) push(el.tagName.toLowerCase() + '[' + a + '="' + String(v).replace(/"/g, '') + '"]');
  }
  const t = (el.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 40);
  if (t && /^(button|a)$/i.test(el.tagName)) push(el.tagName.toLowerCase() + ':has-text("' + t.replace(/"/g, '') + '")');
  let node = el;
  const path = [];
  while (node && node.nodeType === 1 && path.length < 6) {
    let name = node.tagName.toLowerCase();
    const parent = node.parentElement;
    if (parent) {
      const same = Array.from(parent.children).filter(c => c.tagName === node.tagName);
      if (same.length > 1) name += ':nth-of-type(' + (same.indexOf(node) + 1) + ')';
    }
    path.unshift(name);
    node = parent;
  }
  push(path.join(' > '));
  const uniq = cands.map(s => {
    try { return {sel: s, unique: document.querySelectorAll(s).length === 1}; }
    catch (e) { return {sel: s, unique: false}; }
  });
  const r = el.getBoundingClientRect();
  const type = (el.getAttribute('type') || '').toLowerCase();
  let sug = 'click';
  if (/^(input|textarea)$/i.test(el.tagName) && ['checkbox', 'radio', 'submit', 'button'].indexOf(type) < 0) sug = 'fill';
  else if (type === 'checkbox') sug = 'check';
  else if (/^select$/i.test(el.tagName)) sug = 'select';
  return {tag: el.tagName.toLowerCase(), text: t,
          rect: {x: r.x, y: r.y, w: r.width, h: r.height},
          candidates: uniq, suggested: sug};
}"""

HIGHLIGHT_JS = """sel => {
  const old = document.getElementById('__webui_hl');
  if (old) old.remove();
  const el = document.querySelector(sel);
  if (!el) return 'not-found';
  const r = el.getBoundingClientRect();
  const d = document.createElement('div');
  d.id = '__webui_hl';
  d.style.cssText = 'position:fixed;left:' + r.x + 'px;top:' + r.y + 'px;width:' + r.width
    + 'px;height:' + r.height + 'px;outline:3px solid red;z-index:2147483647;pointer-events:none;';
  document.body.appendChild(d);
  return 'ok';
}"""

_inspect: dict[str, InspectSession] = {}
_inspect_lock = threading.Lock()


def _sess(sid: str) -> InspectSession:
    sess = _inspect.get(sid)
    if sess is None:
        raise HTTPException(404, "Сессия инспектора не найдена")
    return sess


@app.post("/api/inspect/start")
def api_inspect_start(payload: dict[str, Any]) -> dict[str, Any]:
    url = str(payload.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "Нужен http(s) URL")
    with _inspect_lock:
        while len(_inspect) >= 3:
            old_id, old = next(iter(_inspect.items()))
            try:
                old.close()
            except Exception:
                pass
            _inspect.pop(old_id, None)
        sid = uuid.uuid4().hex[:8]
        try:
            _inspect[sid] = InspectSession(url)
        except Exception as err:
            raise HTTPException(500, f"Инспектор не открылся: {err}")
        return {"sid": sid}


@app.get("/api/inspect/{sid}/shot")
def api_inspect_shot(sid: str) -> dict[str, Any]:
    sess = _sess(sid)
    try:
        png = sess.call(lambda p: p.screenshot())
    except Exception as err:
        raise HTTPException(500, f"Скриншот не удался: {err}")
    return {"png": base64.b64encode(png).decode(),
            "w": InspectSession.VIEWPORT["width"], "h": InspectSession.VIEWPORT["height"]}


@app.post("/api/inspect/{sid}/pick")
def api_inspect_pick(sid: str, payload: dict[str, Any]) -> dict[str, Any]:
    sess = _sess(sid)
    try:
        dw = max(1.0, float(payload.get("dw", 1)))
        dh = max(1.0, float(payload.get("dh", 1)))
        px = float(payload.get("x", 0)) * InspectSession.VIEWPORT["width"] / dw
        py = float(payload.get("y", 0)) * InspectSession.VIEWPORT["height"] / dh
    except (TypeError, ValueError):
        raise HTTPException(400, "Координаты должны быть числами")
    try:
        found = sess.call(lambda p: p.evaluate(PICK_JS, [px, py]))
    except Exception as err:
        raise HTTPException(500, f"Не удалось распознать элемент: {err}")
    return {"element": found}


@app.post("/api/inspect/{sid}/highlight")
def api_inspect_highlight(sid: str, payload: dict[str, Any]) -> dict[str, Any]:
    sess = _sess(sid)
    selector = str(payload.get("selector", "")).strip()
    if not selector:
        raise HTTPException(400, "Пустой селектор")
    try:
        def _shot(p):
            found = p.evaluate(HIGHLIGHT_JS, selector)
            png = p.screenshot()
            p.evaluate("() => { const o = document.getElementById('__webui_hl'); if (o) o.remove(); }")
            return found, png
        found, png = sess.call(_shot)
    except Exception as err:
        raise HTTPException(500, f"Подсветка не удалась: {err}")
    return {"found": found == "ok", "png": base64.b64encode(png).decode()}


@app.delete("/api/inspect/{sid}")
def api_inspect_close(sid: str) -> dict[str, Any]:
    with _inspect_lock:
        sess = _inspect.pop(sid, None)
    if sess is None:
        raise HTTPException(404, "Сессия инспектора не найдена")
    try:
        sess.close()
    except Exception:
        pass
    return {"ok": True}


def main() -> None:
    parser = argparse.ArgumentParser(description="Universal Autoregister WebUI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
if __name__ == "__main__":
    main()
