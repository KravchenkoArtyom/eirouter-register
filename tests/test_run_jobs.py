"""Жизнь запуска в панели: очередь аккаунтов, лог на диске, журнал и остановка.

Настоящая регистрация подменяется заглушкой (`runs._register`) — проверяем то,
за что отвечает сама панель: одно хранилище на запуск, счётчики, лог с
обрезкой, файлы в `logs/runs/`, поток событий и отказ второму запуску.
"""
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from webui.api import runs
from webui.server import app

LOCAL = "http://127.0.0.1:8765"
client = TestClient(app, base_url=LOCAL)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Запуск, который никуда не ходит: файлы — во временной папке."""
    monkeypatch.setattr(runs, "OUTPUT_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(runs, "RUNS_LOG_DIR", tmp_path / "logs" / "runs")
    monkeypatch.setattr(runs, "FAILURES_DIR", tmp_path / "logs" / "failures")
    monkeypatch.setattr(runs, "_profiles", lambda browser, emit: (object(), "заглушка"))
    with runs._jobs_lock:
        runs._jobs.clear()
    yield tmp_path
    for job in list(runs._jobs.values()):
        job["stop"] = True
    runs.shutdown()


def fake_register(record):
    """Заглушка регистрации: пишет аккаунт в общее хранилище запуска."""

    def register(job, scenario, profiles, store, control, pacing, captcha, proxy, emit):
        index = len(record) + 1
        record.append({"proxy": proxy, "store": id(store), "thread": threading.get_ident()})
        emit(f"[fake] аккаунт {index} готов")
        store.add(f"user{index}@example.test", scenario.get("name", "demo"),
                  password="secret")
        return {"email": f"user{index}@example.test"}

    return register


def start(**payload):
    scenario = client.get("/api/scenarios").json()[0]["name"]
    body = {"scenario": scenario, "count": 1, "mail": "none", "proxy_mode": "direct"}
    body.update(payload)
    response = client.post("/api/runs", json=body)
    assert response.status_code == 200, response.text
    return response.json()["id"]


def finish(job_id, timeout=20.0):
    """Дождаться конца запуска и вернуть последний снимок."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/runs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError(f"запуск не закончился: {job}")


def test_run_saves_accounts_journal_and_log(sandbox, monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(runs, "_register", fake_register(calls))
    job_id = start(count=3)
    job = finish(job_id)
    assert (job["status"], job["done"], job["failed"]) == ("done", 3, 0)
    # хранилище одно на запуск: три аккаунта в одном файле
    assert len({call["store"] for call in calls}) == 1
    saved = json.loads((sandbox / "accounts.json").read_text(encoding="utf-8"))
    assert [item["email"] for item in saved] == [f"user{i}@example.test" for i in (1, 2, 3)]
    # полный лог и итог запуска остались на диске
    log_file = sandbox / "logs" / "runs" / job["log_file"]
    assert "[fake] аккаунт 3 готов" in log_file.read_text(encoding="utf-8")
    journal = json.loads(log_file.with_suffix(".json").read_text(encoding="utf-8"))
    assert journal["status"] == "done" and journal["count"] == 3


def test_log_offset_returns_only_new_lines(sandbox, monkeypatch):
    monkeypatch.setattr(runs, "_register", fake_register([]))
    job = finish(start(count=1))
    assert job["logs"] and job["next_offset"] == len(job["logs"])
    tail = client.get(f"/api/runs/{job['id']}?offset={job['next_offset']}").json()
    assert tail["logs"] == [] and tail["next_offset"] == job["next_offset"]
    assert tail["dropped"] == 0


def test_log_keeps_last_lines_in_memory(sandbox, monkeypatch):
    """Болтливый сценарий не съедает память: старые строки уходят в файл."""
    monkeypatch.setattr(runs, "RUN_LOG_LIMIT", 5)

    def chatty(job, scenario, profiles, store, control, pacing, captcha, proxy, emit):
        for number in range(20):
            emit(f"[fake] строка {number}")
        return {}

    monkeypatch.setattr(runs, "_register", chatty)
    job = finish(start(count=1))
    assert len(job["logs"]) == 5
    assert job["dropped"] >= 15
    assert job["logs"][-1].startswith("[webui] Готово")
    saved = (sandbox / "logs" / "runs" / job["log_file"]).read_text(encoding="utf-8")
    assert "[fake] строка 0" in saved


def test_failed_account_does_not_stop_queue(sandbox, monkeypatch):
    attempts: list[int] = []

    def flaky(job, scenario, profiles, store, control, pacing, captcha, proxy, emit):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("сайт не открылся")
        store.add("ok@example.test", "demo")
        return {}

    monkeypatch.setattr(runs, "_register", flaky)
    job = finish(start(count=2))
    assert (job["status"], job["done"], job["failed"]) == ("done_with_errors", 1, 1)
    assert any("не удался" in line for line in job["logs"])


def test_second_run_is_refused_while_first_works(sandbox, monkeypatch):
    release = threading.Event()

    def slow(job, scenario, profiles, store, control, pacing, captcha, proxy, emit):
        release.wait(10)
        return {}

    monkeypatch.setattr(runs, "_register", slow)
    job_id = start(count=1)
    scenario = client.get("/api/scenarios").json()[0]["name"]
    refused = client.post("/api/runs", json={"scenario": scenario, "count": 1,
                                             "mail": "none", "proxy_mode": "direct"})
    assert refused.status_code == 409
    assert "уже идёт" in refused.json()["detail"]
    release.set()
    assert finish(job_id)["status"] == "done"


def test_stop_ends_queue_early(sandbox, monkeypatch):
    monkeypatch.setattr(runs, "_register", fake_register([]))
    job_id = start(count=50)
    assert client.post(f"/api/runs/{job_id}/stop").status_code == 200
    job = finish(job_id)
    assert job["status"] == "stopped"
    assert job["done"] < 50


def test_workers_run_accounts_in_parallel(sandbox, monkeypatch):
    """Два потока — два аккаунта одновременно, но не больше выбранного числа."""
    seen: list[int] = []
    together = threading.Semaphore(0)
    peak = {"value": 0}
    lock = threading.Lock()

    def parallel(job, scenario, profiles, store, control, pacing, captcha, proxy, emit):
        with lock:
            peak["value"] = max(peak["value"], job["active"])
        seen.append(threading.get_ident())
        together.release()
        time.sleep(0.2)
        return {}

    monkeypatch.setattr(runs, "_register", parallel)
    job = finish(start(count=4, workers=2, delays={"account_min_s": 0, "account_max_s": 0}))
    assert job["done"] == 4 and job["workers"] == 2
    assert len(set(seen)) > 1          # аккаунты шли в разных потоках
    assert peak["value"] <= 2          # но не больше, чем разрешено


def test_stream_sends_state(sandbox, monkeypatch):
    monkeypatch.setattr(runs, "_register", fake_register([]))
    job_id = start(count=1)
    with client.stream("GET", f"/api/runs/{job_id}/stream") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        frames = [line for line in response.iter_lines() if line.startswith("data: ")]
    assert frames, "поток не прислал ни одного кадра"
    last = json.loads(frames[-1][len("data: "):])
    assert last["status"] in ("done", "running")
    assert finish(job_id)["status"] == "done"


def test_captcha_answer_reaches_run(sandbox, monkeypatch):
    answered = threading.Event()

    def waiting(job, scenario, profiles, store, control, pacing, captcha, proxy, emit):
        control.notice("нужна капча", level="warn")
        answered.wait(10)
        return {}

    monkeypatch.setattr(runs, "_register", waiting)
    job_id = start(count=1)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not client.get(f"/api/runs/{job_id}").json()["notices"]:
        time.sleep(0.05)
    job = client.get(f"/api/runs/{job_id}").json()
    assert job["notices"][-1]["message"] == "нужна капча"
    assert client.post(f"/api/runs/{job_id}/captcha",
                       json={"action": "resolved"}).status_code == 200
    answered.set()
    assert finish(job_id)["status"] == "done"


def test_unknown_run_is_404(sandbox):
    assert client.get("/api/runs/нет-такого").status_code == 404
    assert client.get("/api/runs/нет-такого/stream").status_code == 404
    assert client.post("/api/runs/нет-такого/stop").status_code == 404
