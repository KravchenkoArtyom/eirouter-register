"""Временное хранилище элементов, снятых инспектором.

Буфер живёт между перезагрузками страницы и перезапусками WebUI: элемент можно
снять сейчас, а разложить по шагам позже. Хранится одним JSON-файлом рядом с
проектом, в репозиторий не попадает.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

FIELDS = ("selector", "action", "value", "tag", "text", "url", "note", "candidates")


class ElementBuffer:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._items: list[dict[str, Any]] = self._read()

    # ---- файл ----
    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    def _write(self) -> None:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self._items, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        os.replace(temporary, self.path)

    # ---- операции ----
    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._items]

    def add(self, payload: dict[str, Any]) -> dict[str, Any]:
        item = {key: payload.get(key) for key in FIELDS if payload.get(key) is not None}
        item["id"] = uuid.uuid4().hex[:8]
        item["saved_at"] = time.time()
        with self._lock:
            self._items.append(item)
            self._items = self._items[-200:]
            self._write()
        return dict(item)

    def update(self, item_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            for item in self._items:
                if item.get("id") == item_id:
                    item.update({key: payload[key] for key in FIELDS if key in payload})
                    self._write()
                    return dict(item)
        return None

    def remove(self, item_id: str) -> bool:
        with self._lock:
            before = len(self._items)
            self._items = [item for item in self._items if item.get("id") != item_id]
            changed = len(self._items) != before
            if changed:
                self._write()
        return changed

    def clear(self) -> int:
        with self._lock:
            count = len(self._items)
            self._items = []
            self._write()
        return count
