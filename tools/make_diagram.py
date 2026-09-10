"""Собрать PNG-схему из `webui/static/how-it-works.svg`.

SVG — единственный источник: панель показывает его напрямую, а README и
документация ссылаются на PNG, потому что GitHub не рендерит внешние стили
внутри SVG. Запуск: `py -3 tools/make_diagram.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "webui" / "static" / "how-it-works.svg"
TARGET = ROOT / "docs" / "images" / "how-it-works.png"
WIDTH, HEIGHT, SCALE = 1120, 620, 2


def main() -> int:
    from playwright.sync_api import sync_playwright

    svg = SOURCE.read_text(encoding="utf-8")
    page_html = ("<html><body style='margin:0;background:#fbfbfc'>" + svg + "</body></html>")
    temporary = ROOT / "docs" / "images" / "_diagram.html"
    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(page_html, encoding="utf-8")
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT},
                                    device_scale_factor=SCALE)
            page.goto(temporary.as_uri())
            page.screenshot(path=str(TARGET))
            browser.close()
    finally:
        temporary.unlink(missing_ok=True)
    print(f"[diagram] {TARGET.relative_to(ROOT)} обновлён")
    return 0


if __name__ == "__main__":
    sys.exit(main())
