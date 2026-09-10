"""Обнаружение капчи на странице, пауза и уведомление.

Программа не решает капчу и не пытается её обойти: она замечает проверку,
останавливает сценарий, зовёт человека (лог, панель, звук) и ждёт, пока
проверка исчезнет с экрана. Дальше запуск продолжается с того же шага.

Режимы (`CaptchaSettings.mode`):
  wait  — ждать решения человеком (по умолчанию);
  pause — подождать фиксированную паузу и продолжить;
  stop  — прервать аккаунт и перейти к следующему;
  log   — только записать в лог и идти дальше.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any
from collections.abc import Callable

MODES = ("wait", "pause", "stop", "log")

# Признаки известных проверок: сначала конкретные, потом общие.
RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("reCAPTCHA", ('iframe[src*="recaptcha"]', ".g-recaptcha", "#recaptcha",
                   'div[data-sitekey][class*="recaptcha"]')),
    ("hCaptcha", ('iframe[src*="hcaptcha"]', ".h-captcha")),
    ("Cloudflare Turnstile", ('iframe[src*="challenges.cloudflare.com"]', ".cf-turnstile")),
    ("Cloudflare проверка", ("#challenge-form", "#cf-challenge-running",
                             "div.cf-browser-verification")),
    ("Arkose / FunCaptcha", ('iframe[src*="arkoselabs"]', 'iframe[src*="funcaptcha"]',
                             "#funcaptcha")),
    ("GeeTest", (".geetest_panel", ".geetest_holder", 'iframe[src*="geetest"]')),
    ("капча", ('img[src*="captcha"]', 'input[name*="captcha"]', 'div[id*="captcha"]',
               'div[class*="captcha"]', 'iframe[src*="captcha"]')),
)

TEXT_MARKERS = ("я не робот", "i'm not a robot", "i am not a robot", "verify you are human",
                "подтвердите, что вы человек", "подтвердите что вы человек",
                "проверка безопасности", "security check", "enter the captcha",
                "введите капчу", "введите код с картинки")

# Скрипт ищет видимый признак в документе кадра: невидимый контейнер капчи
# висит почти на каждом сайте и сам по себе ничего не значит.
_DETECT_JS = """
(config) => {
  const visible = (node) => {
    if (!node) return false;
    const rect = node.getBoundingClientRect();
    if (rect.width < 8 || rect.height < 8) return false;
    const style = getComputedStyle(node);
    return style.visibility !== 'hidden' && style.display !== 'none'
      && Number(style.opacity || '1') > 0.05;
  };
  for (const rule of config.rules) {
    for (const selector of rule[1]) {
      let nodes = [];
      try { nodes = Array.from(document.querySelectorAll(selector)); } catch (error) { continue; }
      const found = nodes.find(visible);
      if (found) return { kind: rule[0], selector, detail: (found.tagName || '').toLowerCase() };
    }
  }
  if (config.texts.length) {
    const text = (document.body ? document.body.innerText || '' : '').slice(0, 4000).toLowerCase();
    const marker = config.texts.find((item) => text.includes(item));
    if (marker) return { kind: 'проверка «человек ли вы»', selector: '', detail: marker };
  }
  return null;
}
"""


class CaptchaStop(RuntimeError):
    """Пользователь попросил остановить запуск на капче."""


class CaptchaSkipped(RuntimeError):
    """Аккаунт пропущен из-за капчи."""


@dataclass
class CaptchaSettings:
    enabled: bool = True
    mode: str = "wait"
    timeout: float = 300.0
    poll: float = 3.0
    pause_s: float = 20.0
    check_text: bool = True
    selectors: tuple[str, ...] = ()
    notify: bool = True
    sound: bool = True

    @classmethod
    def from_dict(cls, data: Any) -> CaptchaSettings:
        values = data if isinstance(data, dict) else {}

        def number(key: str, default: float) -> float:
            try:
                return max(float(values[key]), 0.0)
            except (KeyError, TypeError, ValueError):
                return default

        selectors = values.get("selectors")
        if isinstance(selectors, str):
            extra = tuple(part.strip() for part in selectors.split(",") if part.strip())
        elif isinstance(selectors, (list, tuple)):
            extra = tuple(str(part).strip() for part in selectors if str(part).strip())
        else:
            extra = ()
        mode = str(values.get("mode") or "wait")
        return cls(enabled=values.get("enabled", True) is not False,
                   mode=mode if mode in MODES else "wait",
                   timeout=number("timeout", 300.0), poll=max(number("poll", 3.0), 0.5),
                   pause_s=number("pause_s", 20.0),
                   check_text=values.get("check_text", True) is not False,
                   selectors=extra,
                   notify=values.get("notify", True) is not False,
                   sound=values.get("sound", True) is not False)

    def as_dict(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "mode": self.mode, "timeout": self.timeout,
                "poll": self.poll, "pause_s": self.pause_s, "check_text": self.check_text,
                "selectors": list(self.selectors), "notify": self.notify, "sound": self.sound}

    def rules(self) -> list[list[Any]]:
        rules = [["своя проверка", list(self.selectors)]] if self.selectors else []
        return rules + [[kind, list(selectors)] for kind, selectors in RULES]


async def detect(page, settings: CaptchaSettings | None = None) -> dict[str, Any] | None:
    """Первый видимый признак капчи в любом кадре страницы или None."""
    settings = settings or CaptchaSettings()
    config = {"rules": settings.rules(),
              "texts": list(TEXT_MARKERS) if settings.check_text else []}
    frames = [page.main_frame] + [frame for frame in page.frames if frame != page.main_frame]
    for frame in frames:
        try:
            found = await frame.evaluate(_DETECT_JS, config)
        except Exception:  # кадр закрылся или не пускает — не наша забота
            continue
        if found:
            found["url"] = frame.url or ""
            return found
    return None


class CaptchaGuard:
    """Проверяет страницу на капчу и решает, что делать дальше."""

    def __init__(self, settings: CaptchaSettings | None = None, control: Any = None,
                 log: Callable[[str], None] = print, pacing: Any = None) -> None:
        self.settings = settings or CaptchaSettings()
        self.control = control
        self.log = log
        self.pacing = pacing
        self.seen = 0

    # ---- основной вход ----
    async def check(self, page, where: str = "", force_wait: bool = False,
                    timeout: float | None = None) -> str:
        """Пусто, если капчи нет; иначе исход ожидания."""
        if not self.settings.enabled and not force_wait:
            return ""
        info = await detect(page, self.settings)
        if info is None:
            return ""
        self.seen += 1
        info["where"] = where
        mode = "wait" if force_wait else self.settings.mode
        if mode == "log":
            self._announce(info)
            return "logged"
        if mode == "stop":
            self._announce(info)
            raise CaptchaSkipped(f"капча «{info['kind']}» на шаге {where or '—'}: "
                                 "аккаунт пропущен по настройке")
        if mode == "pause":
            return await self._pause(page, info)
        return await self._wait_until_clear(page, info, timeout)

    # ---- ожидание человека ----
    async def _pause(self, page, info: dict[str, Any]) -> str:
        """Короткая пауза: даём человеку шанс, но не ждём его до конца.

        Пауза прерывается досрочно, если капча исчезла или человек ответил
        кнопкой в панели, — иначе запуск идёт дальше сам.
        """
        limit = max(0.0, float(self.settings.pause_s))
        step = min(max(0.2, float(self.settings.poll)), max(0.2, limit))
        started = time.monotonic()
        self._announce(info)
        while True:
            await asyncio.sleep(step)
            spent = time.monotonic() - started
            answered = self._answer(spent)
            if answered:
                await self._settle()
                return answered
            if await detect(page, self.settings) is None:
                self._finish("капча исчезла — продолжаю", spent)
                await self._settle()
                return "cleared"
            if spent >= limit:
                self._finish("пауза выдержана", spent)
                return "paused"

    async def _wait_until_clear(self, page, info: dict[str, Any],
                                timeout: float | None = None) -> str:
        limit = float(timeout if timeout is not None else self.settings.timeout)
        started = time.monotonic()
        self._announce(info)
        reminder = started
        while True:
            await asyncio.sleep(self.settings.poll)
            spent = time.monotonic() - started
            answered = self._answer(spent)
            if answered:
                await self._settle()
                return answered
            if await detect(page, self.settings) is None:
                self._finish("капча исчезла — продолжаю", spent)
                await self._settle()
                return "cleared"
            if limit and spent >= limit:
                self._finish(f"не решена за {int(limit)} с", spent)
                raise TimeoutError(f"капча «{info['kind']}» не решена за {int(limit)} с")
            if time.monotonic() - reminder >= 30:
                reminder = time.monotonic()
                self.log(f"[captcha] Всё ещё ждём: {info['kind']}, прошло {int(spent)} с")

    def _answer(self, spent: float) -> str:
        """Разобрать ответ человека из панели или консоли.

        Возвращает «resolved», если человек сказал, что решил капчу, и пустую
        строку, если ответа пока нет. «Пропустить» и «Остановить» поднимают
        исключения, которые ловит запуск.
        """
        answer = self.control.take_answer() if self.control is not None else None
        if answer == "stop" or (self.control is not None and self.control.should_stop):
            self._finish("остановлено пользователем", spent)
            raise CaptchaStop("запуск остановлен на капче")
        if answer == "skip":
            self._finish("аккаунт пропущен", spent)
            raise CaptchaSkipped("аккаунт пропущен на капче")
        if answer == "resolved":
            self._finish("отмечено как решённое", spent)
            return "resolved"
        return ""

    async def _settle(self) -> None:
        if self.pacing is not None:
            await self.pacing.after_captcha()

    # ---- уведомления ----
    def _announce(self, info: dict[str, Any]) -> None:
        if self.control is not None:
            self.control.captcha_started(info)
            return
        self.log(f"[captcha] Обнаружена проверка «{info.get('kind')}» "
                 f"{info.get('selector', '')} — решите её в окне браузера")

    def _finish(self, outcome: str, seconds: float) -> None:
        if self.control is not None:
            self.control.captcha_finished(outcome, seconds)
        else:
            self.log(f"[captcha] {outcome} за {seconds:.0f} с")
