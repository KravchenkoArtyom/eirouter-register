"""Мост между запуском и тем, кто за ним наблюдает.

Регистрация идёт в отдельном потоке (WebUI) или прямо в консоли (CLI), а
события — «появилась капча», «капча решена», «пауза между аккаунтами» — нужны
снаружи. `RunControl` собирает их в одном месте: пишет в лог, отдаёт
слушателю (панель, консоль) и принимает ответ пользователя.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

CAPTCHA_ANSWERS = ("resolved", "skip", "stop")


class RunControl:
    """Потокобезопасное состояние одного запуска."""

    def __init__(self, log: Callable[[str], None] = print,
                 listener: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.log = log
        self.listener = listener
        self._lock = threading.Lock()
        self._stop = False
        self._answer: str | None = None
        self.captcha: dict[str, Any] | None = None
        self.events: list[dict[str, Any]] = []

    # ---- события ----
    def emit(self, event_type: str, message: str = "", **data: Any) -> dict[str, Any]:
        """Событие запуска. Поле `type` — вид события, остальное — подробности
        (у капчи там `kind` с названием проверки)."""
        event = {"type": event_type, "message": message, "at": time.time(), **data}
        with self._lock:
            self.events.append(event)
            del self.events[:-200]
        if self.listener is not None:
            try:
                self.listener(event)
            except Exception:  # слушатель не должен ломать запуск
                pass
        return event

    def notice(self, message: str, level: str = "info", **data: Any) -> None:
        self.log(f"[notify] {message}")
        self.emit("notice", message, level=level, **data)

    # ---- остановка ----
    def request_stop(self) -> None:
        with self._lock:
            self._stop = True
            self._answer = "stop"

    @property
    def should_stop(self) -> bool:
        with self._lock:
            return self._stop

    # ---- капча ----
    def captcha_started(self, info: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._answer = None
            self.captcha = dict(info, since=time.time(), status="waiting")
        self.log(f"[captcha] Обнаружена проверка «{info.get('kind', 'captcha')}»"
                 f"{' на шаге ' + str(info['where']) if info.get('where') else ''}"
                 " — жду решения")
        return self.emit("captcha", "Капча на экране: решите её в окне браузера",
                         level="warn", **info)

    def captcha_finished(self, outcome: str, seconds: float = 0.0) -> None:
        with self._lock:
            self.captcha = None
        self.log(f"[captcha] {outcome} за {seconds:.0f} с")
        self.emit("captcha_done", outcome, level="ok", seconds=round(seconds, 1))

    def answer_captcha(self, answer: str) -> str:
        """Ответ пользователя из панели или консоли."""
        value = answer if answer in CAPTCHA_ANSWERS else "resolved"
        with self._lock:
            self._answer = value
            if value == "stop":
                self._stop = True
        return value

    def take_answer(self) -> str | None:
        with self._lock:
            answer, self._answer = self._answer, None
            return answer

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"stop": self._stop,
                    "captcha": dict(self.captcha) if self.captcha else None}
