"""Прокси: разбор, файл со списком и ротация между аккаунтами.

Прокси можно задать тремя способами: файлом (`proxies.txt` или любой другой,
загруженный в панели), списком строк из панели и одной строкой вручную.
Дальше ими распоряжается `ProxyRotation`: выдаёт адрес каждому аккаунту по
выбранному правилу и убирает из очереди те, что стабильно падают.

Поддерживаются `http`, `https` и `socks5` (он же `socks5h`). У socks5 нельзя
указать логин и пароль: Chromium их не принимает, поэтому такая строка
отклоняется с понятным сообщением.
"""

import os
import random
import threading
from pathlib import Path
from typing import Any
from collections.abc import Iterable
from urllib.parse import unquote, urlsplit

SCHEMES = ("http", "https", "socks5", "socks5h")
FILE_MODE = 0o600


def normalize_proxy(raw: str) -> str:
    raw = raw.strip()
    if "://" not in raw:
        raw = "http://" + raw
    try:
        parsed = urlsplit(raw)
        scheme = parsed.scheme.lower()
        if scheme.startswith("socks") and scheme not in ("socks5", "socks5h"):
            raise ValueError("SOCKS4 is not supported: use socks5:// or an http[s] proxy")
        if scheme in ("socks5", "socks5h") and (parsed.username or parsed.password):
            raise ValueError("SOCKS5 with a login is not supported by Chromium: "
                             "use socks5://host:port or an http[s] proxy")
        if scheme not in SCHEMES or not parsed.hostname or not parsed.port:
            raise ValueError
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError
    except ValueError as error:
        if str(error):
            raise
        raise ValueError("Invalid proxy: use http[s]://[user:password@]host:port "
                         "or socks5://host:port") from None
    return raw.rstrip("/")


def is_socks(proxy: str) -> bool:
    return urlsplit(normalize_proxy(proxy)).scheme.lower() in ("socks5", "socks5h")


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
            raise ValueError(f"{path.name}, строка {number}: "
                             f"{explain_bad_proxy(line.strip())}") from None
    return list(dict.fromkeys(proxies))


def explain_bad_proxy(raw: str) -> str:
    """Человеческое объяснение, почему строка не годится как прокси."""
    scheme = raw.split("://")[0].lower() if "://" in raw else ""
    if scheme.startswith("socks") and scheme not in ("socks5", "socks5h"):
        return "поддерживается только socks5, нужен socks5:// или http/https"
    if scheme in ("socks5", "socks5h") and "@" in raw:
        return "socks5 с логином Chromium не принимает — оставьте socks5://host:port"
    return ("не похоже на прокси: нужен http[s]://[логин:пароль@]хост:порт "
            "или socks5://хост:порт")


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
            errors.append(f"строка {number}: {explain_bad_proxy(raw)} — {raw[:60]}")
    return list(dict.fromkeys(proxies)), errors


def save_list(path: Path, proxies: Iterable[str]) -> int:
    """Записать список прокси в файл (по одному в строке). Возвращает счёт."""
    path = Path(path)
    items = list(dict.fromkeys(normalize_proxy(item) for item in proxies))
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(items)
    # В строках бывают логины и пароли: пишем атомарно и «только владельцу».
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(body + ("\n" if body else ""), encoding="utf-8")
    try:
        os.chmod(temporary, FILE_MODE)
    except OSError:  # Windows / чужая ФС — права наследуются от папки
        pass
    os.replace(temporary, path)
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

    Очередь потокобезопасна: при нескольких параллельных аккаунтах `take()` и
    `report()` зовут из разных потоков.
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
        self._lock = threading.Lock()

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
        with self._lock:
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
        with self._lock:
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
        with self._lock:
            return [{"proxy": redact_proxy(proxy), "ok": self.stats[proxy]["ok"],
                     "fail": self.stats[proxy]["fail"], "banned": proxy in self.banned,
                     "current": proxy == self.current}
                    for proxy in self.proxies]

    def summary(self) -> str:
        if not self.proxies:
            return "без прокси (напрямую)"
        return (f"{len(self.alive)} из {len(self.proxies)} прокси, "
                f"{ROTATION_LABELS.get(self.mode, self.mode)}")
