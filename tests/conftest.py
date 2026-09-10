"""Общая настройка тестов: корень проекта в sys.path и пропуск тестов без Chrome.

Часть тестов поднимает настоящий Google Chrome (`channel="chrome"`) — именно
им пользуется программа. На машине, где Chrome не установлен (например, в
чистом CI), такие тесты помечаются как пропущенные, а не как упавшие:
поставьте Chrome (`playwright install chrome`), чтобы прогнать и их.
"""
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CHROME_PATHS = (
    "/opt/google/chrome/chrome",
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)


def chrome_installed() -> bool:
    """Есть ли настоящий Chrome (встроенный в Playwright Chromium не считается)."""
    if any(shutil.which(name) for name in ("google-chrome", "google-chrome-stable")):
        return True
    return any(Path(path).exists() for path in CHROME_PATHS)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "chrome: тесту нужен установленный Google Chrome")


def pytest_collection_modifyitems(config: pytest.Config,
                                  items: list[pytest.Item]) -> None:
    if chrome_installed():
        return
    skip = pytest.mark.skip(reason="Google Chrome не найден: поставьте его "
                                   "командой playwright install chrome")
    for item in items:
        if "chrome" in item.keywords:
            item.add_marker(skip)
