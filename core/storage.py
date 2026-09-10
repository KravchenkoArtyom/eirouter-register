"""Потокобезопасное хранилище аккаунтов в JSON-файле.

Запись атомарная (временный файл + `os.replace`): обрыв процесса не портит
файл. Имя временного файла уникально для каждого хранилища, поэтому два
параллельных запуска не мешают друг другу. Статусы пишутся фоновым писарем с
задержкой, структурные изменения (add/remove) — сразу.

Хранилище — ресурс с потоком: заведите его на запуск (не на аккаунт) и
закройте через `close()` или `with AccountStore(path) as store`.

Файл содержит пароли, поэтому создаётся с правами «только владелец» (на
Windows права наследуются от папки — храните проект в своём профиле).
"""

import json
import os
import threading
import uuid
from datetime import datetime, UTC
from pathlib import Path

SAVE_DEBOUNCE_SEC = 2.0
FILE_MODE = 0o600


class AccountStore:
    """Все операции с данными обёрнуты в lock: безопасны из любого потока."""

    def __init__(self, path="accounts.json"):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._io_lock = threading.Lock()
        self._accounts = []
        self._dirty = False
        self._closed = threading.Event()
        self._tmp = self.path.with_suffix(f".json.{os.getpid()}-{uuid.uuid4().hex[:8]}.tmp")
        self.load()
        self._writer = threading.Thread(target=self._writer_loop, daemon=True,
                                        name=f"store-writer-{self.path.stem}")
        self._writer.start()

    # ---- жизненный цикл ----
    def close(self, timeout: float = 5.0):
        """Дописать отложенное и остановить писаря."""
        if self._closed.is_set():
            return
        self._closed.set()
        self._writer.join(timeout=timeout)
        with self._lock:
            dirty = self._dirty
            self._dirty = False
        if dirty:
            try:
                self.save()
            except OSError as e:
                print(f"[storage] финальная запись не удалась: {e}")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
        return False

    # ---- диск ----
    def load(self):
        with self._lock:
            if not self.path.exists():
                self._accounts = []
                return
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, list):
                    raise ValueError("корень файла должен быть списком аккаунтов")
                self._accounts = [item for item in data if isinstance(item, dict)]
                return
            except (json.JSONDecodeError, OSError, ValueError) as e:
                # не затираем данные: убираем битый файл с дороги и сообщаем
                backup = self.path.with_suffix(".json.corrupt")
                try:
                    self.path.replace(backup)
                    print(f"[storage] {self.path.name} повреждён ({e}); "
                          f"сохранён как {backup.name}")
                except OSError:
                    print(f"[storage] {self.path.name} повреждён ({e}); "
                          "бэкап создать не удалось")
                self._accounts = []

    def save(self):
        """Синхронная запись — для структурных изменений (add/remove)."""
        with self._lock:
            data = json.dumps(self._accounts, ensure_ascii=False, indent=2)
        self._atomic_write(data)

    def _schedule_save(self):
        """Отложенная запись для статусов."""
        with self._lock:
            self._dirty = True

    def _writer_loop(self):
        while not self._closed.wait(SAVE_DEBOUNCE_SEC):
            with self._lock:
                if not self._dirty:
                    continue
                self._dirty = False
                data = json.dumps(self._accounts, ensure_ascii=False, indent=2)
            try:
                self._atomic_write(data)
            except OSError as e:
                print(f"[storage] отложенная запись не удалась: {e}")
                with self._lock:
                    self._dirty = True

    def _atomic_write(self, data):
        # _io_lock сериализует запись внутри хранилища, уникальное имя tmp —
        # между хранилищами (два запуска в один файл).
        with self._io_lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._tmp.write_text(data, encoding="utf-8")
            try:
                os.chmod(self._tmp, FILE_MODE)
            except OSError:  # Windows / чужая ФС — права наследуются от папки
                pass
            os.replace(self._tmp, self.path)

    # ---- чтение ----
    def all(self):
        with self._lock:
            return [dict(a) for a in self._accounts]

    def get(self, email):
        with self._lock:
            for a in self._accounts:
                if a.get("email") == email:
                    return dict(a)
        return None

    # ---- структурные изменения (мгновенное сохранение) ----
    def add(self, email, provider, token="", api_key="", base_url="",
            password="", device_uuid=None, status="active"):
        with self._lock:
            # дедупликация по api_key: повторный импорт не плодит копии,
            # а обновляет base_url и воскрешает аккаунт
            if api_key:
                for a in self._accounts:
                    if a.get("api_key") == api_key:
                        if base_url:
                            a["base_url"] = base_url
                        a["status"] = status
                        self.save()
                        return dict(a)
            account = {
                "email": email,
                "password": password,
                "provider": provider,
                "token": token or "",
                "api_key": api_key or "",
                "base_url": base_url or "",
                "device_uuid": device_uuid or str(uuid.uuid4()),
                "status": status,
                "created_at": datetime.now(UTC).isoformat(),
            }
            self._accounts.append(account)
        self.save()
        return dict(account)

    def remove(self, email):
        self.remove_many([email])

    def remove_many(self, emails):
        with self._lock:
            drop = set(emails)
            self._accounts = [a for a in self._accounts if a.get("email") not in drop]
        self.save()

    def set_api_key(self, email, api_key, base_url=None):
        with self._lock:
            for a in self._accounts:
                if a.get("email") == email:
                    a["api_key"] = api_key
                    if base_url:
                        a["base_url"] = base_url
                    break
        self.save()

    # ---- статусы (отложенное сохранение) ----
    def set_status(self, email, status):
        """status: 'registration_pending' | 'active' | 'no_key' | ваш собственный."""
        with self._lock:
            for a in self._accounts:
                if a.get("email") == email:
                    a["status"] = status
                    break
        self._schedule_save()

    def active(self, provider=None):
        """Аккаунты со статусом active, при необходимости — одного провайдера."""
        with self._lock:
            return [dict(a) for a in self._accounts
                    if a.get("status") == "active"
                    and (not provider or a.get("provider") == provider)]
