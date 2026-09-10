"""Документация не отстаёт от кода: ссылки живые, схема совпадает с панелью.

Дешёвая страховка от «документация врёт»: битую ссылку и забытую копию схемы
проще поймать тестом, чем глазами.
"""
import re
from pathlib import Path

import pytest

from tools import make_diagram

ROOT = Path(__file__).resolve().parent.parent
MARKDOWN = sorted(list(ROOT.glob("*.md")) + list((ROOT / "docs").glob("*.md")))
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def test_diagram_copy_matches_panel():
    assert make_diagram.in_sync(), (
        "docs/images/how-it-works.svg отстал: запустите py -3 tools/make_diagram.py")
    assert make_diagram.main(["--check"]) == 0


def test_diagram_sync_restores_copy(monkeypatch, tmp_path):
    source = tmp_path / "source.svg"
    target = tmp_path / "docs" / "copy.svg"
    source.write_text("<svg>схема</svg>", encoding="utf-8")
    monkeypatch.setattr(make_diagram, "SOURCE", source)
    monkeypatch.setattr(make_diagram, "TARGET", target)
    monkeypatch.setattr(make_diagram, "ROOT", tmp_path)
    assert make_diagram.main(["--check"]) == 1
    assert make_diagram.sync() is True
    assert target.read_text(encoding="utf-8") == "<svg>схема</svg>"
    assert make_diagram.sync() is False and make_diagram.main([]) == 0


@pytest.mark.parametrize("document", MARKDOWN, ids=lambda path: path.name)
def test_local_links_point_to_existing_files(document):
    for target in LINK.findall(document.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        path = (document.parent / target.split("#")[0]).resolve()
        assert path.exists(), f"{document.name}: ссылка {target} никуда не ведёт"


def test_docs_do_not_promise_removed_folders():
    """В проекте не осталось `legacy/`, и документация об этом знает."""
    assert not (ROOT / "legacy").exists()
    for document in MARKDOWN:
        text = document.read_text(encoding="utf-8")
        assert "legacy/" not in text, f"{document.name} всё ещё зовёт в legacy/"
