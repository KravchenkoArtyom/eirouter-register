"""HTTP CONNECT прокси: разбор, файл со списком и ротация между аккаунтами.

Прокси можно задать тремя способами: файлом (`proxies.txt` или любой другой,
загруженный в панели), списком строк из панели и одной строкой вручную.
Дальше ими распоряжается `ProxyRotation`: выдаёт адрес каждому аккаунту по
выбранному правилу и убирает из очереди те, что стабильно падают.
"""

import random
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit


def normalize_proxy(raw: str) -> str:
    raw = raw.strip()
    if "://" not in raw:
        raw = "http://" + raw
    try:
        parsed = urlsplit(raw)
        if parsed.scheme.lower().startswith("socks"):
            raise ValueError("SOCKS proxy is not supported: use an http[s] proxy")
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or not parsed.port:
            raise ValueError
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError
    except ValueError as error:
        if str(error):
            raise
        raise ValueError("Invalid proxy: use http[s]://[user:password@]host:port") from None
    return raw.rstrip("/")


def playwright_proxy(proxy: str) -> dict[str, str]:
    parsed = urlsplit(normalize_proxy(proxy))
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    result = {"server": f"{parsed.scheme}://{host}:{parsed.port}"}
    if parsed.username is not None:
        result["username"] = unquote(parsed.username)
    if parsed.password is not None:
        result["password"] = unquote(parsed.password)
    return result


def redact_proxy(proxy: str) -> str:
    return playwright_proxy(proxy)["server"]


def load_list(path: Path) -> list[str]:
    if not path.exists():
        return []
    proxies = []
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            proxies.append(normalize_proxy(line))
        except ValueError:
            raise ValueError(f"Invalid proxy on line {number} of {path.name}") from None
    return list(dict.fromkeys(proxies))


def parse_lines(text: str) -> tuple[list[str], list[str]]:
    """Разобрать список прокси из текста.

    Возвращает нормализованные адреса без повторов и человекочитаемые ошибки
    по номерам строк — панель показывает их рядом с полем ввода.
    """
    proxies: list[str] = []
    errors: list[str] = []
    for number, line in enumerate((text or "").splitlines(), 1):
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        try:
            proxies.append(normalize_proxy(raw))
        except ValueError:
            reason = ("socks не поддерживается, нужен http/https"
                      if raw.split("://")[0].lower().startswith("socks")
                      else "не похоже на прокси")
            errors.append(f"строка {number}: {reason} — {raw[:60]}")
    return list(dict.fromkeys(proxies)), errors


def save_list(path: Path, proxies: Iterable[str]) -> int:
    """Записать список прокси в файл (по одному в строке). Возвращает счёт."""
    path = Path(path)
    items = list(dict.fromkeys(normalize_proxy(item) for item in proxies))
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(items)
    path.write_text(body + ("\n" if body else ""), encoding="utf-8")
    return len(items)


ROTATIONS: tuple[str, ...] = ("per_account", "random", "sticky", "on_error")
ROTATION_LABELS = {
    "per_account": "по кругу: новый аккаунт — новый прокси",
    "random": "случайный на каждый аккаунт",
    "sticky": "один и тот же, пока работает",
    "on_error": "менять только после ошибки",
}


class ProxyRotation:
    """Очередь прокси на запуск.

    `take()` выдаёт адрес следующему аккаунту, `report()` сообщает об исходе.
    После `ban_after` неудач подряд адрес выпадает из очереди — если выпали
    все, запуск идёт напрямую, о чём пишется в лог.
    """

    def __init__(self, proxies: Iterable[str], mode: str = "per_account",
                 ban_after: int = 0) -> None:
        self.proxies = [normalize_proxy(item) for item in proxies]
        self.mode = mode if mode in ROTATIONS else "per_account"
        self.ban_after = max(int(ban_after or 0), 0)
        self.index = -1
        self.current: str | None = None
        self.stats: dict[str, dict[str, int]] = {
            proxy: {"ok": 0, "fail": 0, "streak": 0} for proxy in self.proxies}
        self.banned: set[str] = set()

    def __len__(self) -> int:
        return len(self.proxies)

    @property
    def alive(self) -> list[str]:
        return [proxy for proxy in self.proxies if proxy not in self.banned]

    def _next_alive(self) -> str | None:
        """Следующий незабаненный адрес по кругу от текущей позиции."""
        total = len(self.proxies)
        for offset in range(1, total + 1):
            position = (self.index + offset) % total
            candidate = self.proxies[position]
            if candidate not in self.banned:
                self.index = position
                return candidate
        return None

    def take(self) -> str | None:
        """Адрес для следующего аккаунта или None, если идём напрямую."""
        if not self.alive:
            self.current = None
            return None
        if self.mode == "random":
            self.current = random.choice(self.alive)
        elif self.mode in ("sticky", "on_error"):
            # Держим один адрес; после неудачи `report` сбрасывает current,
            # и очередь переходит к следующему.
            if self.current is None or self.current in self.banned:
                self.current = self._next_alive()
        else:  # per_account
            self.current = self._next_alive()
        return self.current

    def report(self, proxy: str | None, ok: bool) -> None:
        if not proxy or proxy not in self.stats:
            return
        stats = self.stats[proxy]
        if ok:
            stats["ok"] += 1
            stats["streak"] = 0
            return
        stats["fail"] += 1
        stats["streak"] += 1
        if self.mode in ("on_error", "sticky") and self.current == proxy:
            self.current = None  # следующий аккаунт возьмёт другой адрес
        if self.ban_after and stats["streak"] >= self.ban_after:
            self.banned.add(proxy)

    def describe(self) -> list[dict[str, Any]]:
        """Состояние очереди без логинов и паролей — для панели и логов."""
        return [{"proxy": redact_proxy(proxy), "ok": self.stats[proxy]["ok"],
                 "fail": self.stats[proxy]["fail"], "banned": proxy in self.banned,
                 "current": proxy == self.current}
                for proxy in self.proxies]

    def summary(self) -> str:
        if not self.proxies:
            return "без прокси (напрямую)"
        return (f"{len(self.alive)} из {len(self.proxies)} прокси, "
                f"{ROTATION_LABELS.get(self.mode, self.mode)}")
