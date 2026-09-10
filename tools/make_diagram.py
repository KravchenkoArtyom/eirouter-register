"""Синхронизировать схему `how-it-works.svg` из панели в документацию.

Схема — один файл, `webui/static/how-it-works.svg`: панель показывает его
напрямую. README и документация ссылаются на копию в `docs/images/`, потому
что относительные ссылки Markdown считаются от папки файла. Инструмент
копирует источник в копию и умеет только проверять:

    py -3 tools/make_diagram.py            # обновить копию
    py -3 tools/make_diagram.py --check    # ничего не менять, только сверить

Ту же сверку делает тест `tests/test_docs.py`, поэтому забытая копия не уедет
в репозиторий незамеченной.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "webui" / "static" / "how-it-works.svg"
TARGET = ROOT / "docs" / "images" / "how-it-works.svg"


def in_sync() -> bool:
    """Совпадает ли копия в документации с источником."""
    if not TARGET.exists():
        return False
    return TARGET.read_bytes() == SOURCE.read_bytes()


def sync() -> bool:
    """Обновить копию. True — файл изменился."""
    if in_sync():
        return False
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, TARGET)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Синхронизировать схему проекта")
    parser.add_argument("--check", action="store_true",
                        help="только проверить, что копия не отстала")
    arguments = parser.parse_args(argv)
    relative = TARGET.relative_to(ROOT)
    if arguments.check:
        if in_sync():
            print(f"[diagram] {relative} совпадает с источником")
            return 0
        print(f"[diagram] {relative} отстал: запустите py -3 tools/make_diagram.py")
        return 1
    print(f"[diagram] {relative} " + ("обновлён" if sync() else "уже совпадал"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
