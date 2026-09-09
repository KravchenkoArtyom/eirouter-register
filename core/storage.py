"""Потокобезопасное хранилище пула аккаунтов в accounts.json.

Запись атомарная (временный файл + os.replace): обрыв процесса не портит файл.
Квоты и статусы (телеметрия) пишутся фоновым писарем с задержкой, структурные
изменения (add/remove/import/reset) сохраняются сразу.
"""

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

SAVE_DEBOUNCE_SEC = 2.0


class AccountStore:
    """Все операции с данными обёрнуты в lock: безопасны из UI и из прокси-потока."""

    def __init__(self, path="accounts.json"):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._io_lock = threading.Lock()
        self._accounts = []
        self._dirty = False
        self.load()
        self._writer = threading.Thread(target=self._writer_loop, daemon=True,
                                        name="store-writer")
        self._writer.start()

    # ---- диск ----
    def load(self):
        with self._lock:
            if not self.path.exists():
                self._accounts = []
                return
            try:
                self._accounts = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                # не затираем данные: убираем битый файл с дороги и сообщаем
                backup = self.path.with_suffix(".json.corrupt")
                try:
                    self.path.replace(backup)
                    print(f"[storage] accounts.json повреждён ({e}); сохранён как {backup.name}")
                except OSError:
                    print(f"[storage] accounts.json повреждён ({e}); бэкап создать не удалось")
                self._accounts = []

    def save(self):
        """Синхронная запись - для структурных изменений (add/remove/import/reset)."""
        with self._lock:
            data = json.dumps(self._accounts, ensure_ascii=False, indent=2)
        self._atomic_write(data)

    def _schedule_save(self):
        """Отложенная запись для телеметрии (квоты/статусы)."""
        with self._lock:
            self._dirty = True

    def _writer_loop(self):
        while True:
            time.sleep(SAVE_DEBOUNCE_SEC)
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
        # _io_lock сериализует саму запись: снимок сериализуется под _lock,
        # а два параллельных writer'а не должны писать в один и тот же tmp
        with self._io_lock:
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, self.path)

    # ---- чтение ----
    def all(self):
        with self._lock:
            return [dict(a) for a in self._accounts]

    def get(self, email):
        with self._lock:
            for a in self._accounts:
                if a["email"] == email:
                    return dict(a)
        return None

    # ---- структурные изменения (мгновенное сохранение) ----
    def add(self, email, provider, token="", api_key="", base_url="",
            password="", device_uuid=None, status="active", oauth_token="",
            plan_state="unknown"):
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
                "oauth_token": oauth_token or "",
                "base_url": base_url or "",
                "device_uuid": device_uuid or str(uuid.uuid4()),
                "status": status,
                "plan_state": plan_state,
                "plan_reason": "",
                "plan_checked_at": 0.0,
                "plan_retry_at": 0.0,
                "plan_attempts": 0,
                "quota": {},  # {model: остаток}
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._accounts.append(account)
        self.save()
        return dict(account)

    def remove(self, email):
        self.remove_many([email])

    def remove_many(self, emails):
        with self._lock:
            drop = set(emails)
            self._accounts = [a for a in self._accounts if a["email"] not in drop]
        self.save()

    def set_api_key(self, email, api_key, base_url=None):
        with self._lock:
            for a in self._accounts:
                if a["email"] == email:
                    a["api_key"] = api_key
                    if base_url:
                        a["base_url"] = base_url
                    break
        self.save()

    def reset_statuses(self, status="active"):
        with self._lock:
            for a in self._accounts:
                if a.get("status") not in {"pending_plan", "awaiting_activation"}:
                    a["status"] = status
                a["quota"] = {}  # сбрасываем и пометки об исчерпании по моделям
        self.save()

    def import_json(self, data):
        with self._lock:
            items = data if isinstance(data, list) else [data]
            for a in items:
                a.setdefault("provider", "zcode")
                a.setdefault("password", "")
                a.setdefault("token", "")
                a.setdefault("api_key", "")
                a.setdefault("oauth_token", "")
                a.setdefault("base_url", "")
                a.setdefault("device_uuid", str(uuid.uuid4()))
                a.setdefault("status", "active")
                a.setdefault("plan_state", "unknown")
                a.setdefault("plan_reason", "")
                a.setdefault("plan_checked_at", 0.0)
                a.setdefault("plan_retry_at", 0.0)
                a.setdefault("plan_attempts", 0)
                if not isinstance(a.get("quota"), dict):
                    a["quota"] = {}
                a.setdefault("created_at", datetime.now(timezone.utc).isoformat())
                self._accounts.append(a)
        self.save()

    # ---- телеметрия (отложенное сохранение) ----
    def set_status(self, email, status):
        """status: 'active' | 'exhausted' | 'banned' | 'no_key'."""
        with self._lock:
            for a in self._accounts:
                if a["email"] == email:
                    a["status"] = status
                    break
        self._schedule_save()

    def set_plan_state(self, email, state, reason=""):
        """Persist ZCode plan availability and schedule only pending retries."""
        retry_delays = (15, 30, 60, 120, 300)
        now = time.time()
        with self._lock:
            for a in self._accounts:
                if a["email"] != email:
                    continue
                a["plan_state"] = state
                a["plan_reason"] = reason or ""
                a["plan_checked_at"] = now
                if state == "pending":
                    attempts = int(a.get("plan_attempts") or 0) + 1
                    a["plan_attempts"] = attempts
                    delay = retry_delays[min(attempts - 1, len(retry_delays) - 1)]
                    if attempts > len(retry_delays):
                        delay = 900
                    a["plan_retry_at"] = now + delay
                    a["status"] = "pending_plan"
                elif state == "available":
                    a["plan_attempts"] = 0
                    a["plan_retry_at"] = 0.0
                    a["status"] = "active"
                elif state == "auth_error":
                    a["plan_retry_at"] = 0.0
                    a["status"] = "no_key"
                elif state == "awaiting_activation":
                    a["plan_attempts"] = 0
                    a["plan_retry_at"] = 0.0
                    a["status"] = "awaiting_activation"
                else:
                    a["plan_retry_at"] = 0.0
                    a["status"] = "no_plan"
                break
        self._schedule_save()

    def quota_candidates(self, provider=None, force=False):
        """Accounts eligible for quota refresh; activation waits require manual refresh."""
        now = time.time()
        with self._lock:
            result = []
            for a in self._accounts:
                if provider and a.get("provider") != provider:
                    continue
                if a.get("status") == "active":
                    result.append(dict(a))
                elif (a.get("provider") == "zcode" and a.get("status") == "pending_plan"
                      and (force or float(a.get("plan_retry_at") or 0) <= now)):
                    result.append(dict(a))
                elif (a.get("provider") == "zcode"
                      and a.get("status") == "awaiting_activation" and force):
                    result.append(dict(a))
            return result

    def set_proxy(self, emails, proxy=""):
        """Индивидуальный прокси аккаунта (для ротатора и обновления квоты)."""
        with self._lock:
            drop = set(emails)
            for a in self._accounts:
                if a["email"] in drop:
                    a["proxy"] = (proxy or "").strip()
        self.save()

    def set_quota(self, email, model, quota):
        """Остаток квоты аккаунта по конкретной модели."""
        with self._lock:
            for a in self._accounts:
                if a["email"] == email:
                    if not isinstance(a.get("quota"), dict):
                        a["quota"] = {}
                    a["quota"][model] = quota
                    break
        self._schedule_save()

    # ---- агрегаты ----
    def total_quota(self, provider=None):
        """Суммарный остаток по каждой модели: {model: (сумма, аккаунтов с данными)}."""
        totals = {}
        for a in self.active(provider):
            quota = a.get("quota") or {}
            for model, val in quota.items():
                if isinstance(val, (int, float)):
                    t, k = totals.get(model, (0, 0))
                    totals[model] = (t + val, k + 1)
        return totals

    def known_models(self):
        """Все модели, встреченные в квотах аккаунтов (автоподхват новых версий)."""
        models = set()
        with self._lock:
            for a in self._accounts:
                for m in (a.get("quota") or {}):
                    models.add(m)
        return models

    def active(self, provider=None, model=None):
        """Живые аккаунты. Если передан model (для кредитных провайдеров -
        ключ квоты, например 'qoder-credits') - отсекаем те, где остаток
        по нему сгорел (0 или меньше)."""
        with self._lock:
            result = []
            for a in self._accounts:
                if a.get("status") != "active":
                    continue
                if provider and a.get("provider") != provider:
                    continue
                if model:
                    quota = a.get("quota") or {}
                    left = quota.get(model)
                    # кредиты могут уйти в минус при последнем запросе -
                    # аккаунт с <= 0 больше не выбираем
                    if isinstance(left, (int, float)) and left <= 0:
                        continue
                result.append(dict(a))
            return result

    def export(self):
        with self._lock:
            return json.loads(json.dumps(self._accounts))
