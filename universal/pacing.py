"""Задержки запуска: между шагами, после капчи и между аккаунтами.

Пауза — не «обход защиты», а вежливость к сайту и время человеку увидеть, что
происходит в окне браузера. Значения задаются в панели (раздел «Запуск») или
в сценарии полем `delays`, поэтому один сценарий может быть быстрым, а другой —
намеренно медленным.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Any

DEFAULTS = {
    "step_min_ms": 150,
    "step_max_ms": 600,
    "click_extra_ms": 250,
    "after_captcha_s": 3,
    "account_min_s": 0,
    "account_max_s": 0,
}


def _number(data: dict[str, Any], key: str, default: float) -> float:
    try:
        value = float(data[key])
    except (KeyError, TypeError, ValueError):
        return float(default)
    return max(value, 0.0)


@dataclass
class Pacing:
    """Набор задержек одного запуска."""

    step_min_ms: float = DEFAULTS["step_min_ms"]
    step_max_ms: float = DEFAULTS["step_max_ms"]
    click_extra_ms: float = DEFAULTS["click_extra_ms"]
    after_captcha_s: float = DEFAULTS["after_captcha_s"]
    account_min_s: float = DEFAULTS["account_min_s"]
    account_max_s: float = DEFAULTS["account_max_s"]
    slow_actions: tuple[str, ...] = field(default=("click", "press", "select", "check",
                                                   "uncheck"))

    @classmethod
    def from_dict(cls, data: Any) -> "Pacing":
        values = data if isinstance(data, dict) else {}
        return cls(step_min_ms=_number(values, "step_min_ms", DEFAULTS["step_min_ms"]),
                   step_max_ms=_number(values, "step_max_ms", DEFAULTS["step_max_ms"]),
                   click_extra_ms=_number(values, "click_extra_ms", DEFAULTS["click_extra_ms"]),
                   after_captcha_s=_number(values, "after_captcha_s", DEFAULTS["after_captcha_s"]),
                   account_min_s=_number(values, "account_min_s", DEFAULTS["account_min_s"]),
                   account_max_s=_number(values, "account_max_s", DEFAULTS["account_max_s"]))

    def as_dict(self) -> dict[str, float]:
        return {"step_min_ms": self.step_min_ms, "step_max_ms": self.step_max_ms,
                "click_extra_ms": self.click_extra_ms,
                "after_captcha_s": self.after_captcha_s,
                "account_min_s": self.account_min_s, "account_max_s": self.account_max_s}

    # ---- расчёт ----
    def step_seconds(self, action: str = "") -> float:
        low, high = sorted((self.step_min_ms, self.step_max_ms))
        if high <= 0:
            return 0.0
        extra = self.click_extra_ms if action in self.slow_actions else 0.0
        return (random.uniform(low, high) + extra) / 1000

    def account_seconds(self) -> float:
        low, high = sorted((self.account_min_s, self.account_max_s))
        return random.uniform(low, high) if high > 0 else 0.0

    # ---- ожидание ----
    async def before_step(self, action: str = "") -> None:
        delay = self.step_seconds(action)
        if delay > 0:
            await asyncio.sleep(delay)

    async def after_captcha(self) -> None:
        if self.after_captcha_s > 0:
            await asyncio.sleep(self.after_captcha_s)
