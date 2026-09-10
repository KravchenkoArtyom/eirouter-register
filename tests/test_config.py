"""Настройки панели: значения по умолчанию, причёсывание и запись на диск.

`webui.local.json` правит и человек, и панель, поэтому мусор в файле не должен
ломать запуск: чужие ключи отбрасываются, неверные значения заменяются.
"""
import json

import pytest

from webui import config


@pytest.fixture
def local_config(tmp_path, monkeypatch):
    target = tmp_path / "webui.local.json"
    monkeypatch.setattr(config, "CONFIG_FILE", target)
    return target


def test_defaults_are_returned_without_file(local_config):
    data = config.load()
    assert set(data) == set(config.SECTIONS)
    assert data["proxy"] == {"mode": "per_account", "ban_after": 2, "source": "file"}
    assert data["notify"] == {"sound": True, "desktop": True, "toast": True}


def test_save_writes_only_known_sections(local_config):
    saved = config.save({"proxy": {"mode": "random"}, "лишнее": {"да": 1}})
    assert saved["proxy"]["mode"] == "random"
    assert "лишнее" not in saved
    on_disk = json.loads(local_config.read_text(encoding="utf-8"))
    assert set(on_disk) == set(config.SECTIONS)
    assert config.load()["proxy"]["mode"] == "random"      # прочитано обратно
    assert config.load()["proxy"]["ban_after"] == 2        # остальное не сбилось


def test_normalize_fixes_impossible_values():
    fixed = config.normalize({"captcha": {"mode": "звонок другу", "timeout": "долго"},
                              "delays": {"step_min_ms": -100, "account_max_s": "мигом"},
                              "proxy": {"mode": "по-настроению", "ban_after": -5,
                                        "source": "телепатия"},
                              "notify": {"sound": "да", "desktop": 0}})
    assert fixed["captcha"]["mode"] == "wait"
    assert fixed["delays"]["step_min_ms"] >= 0
    assert fixed["proxy"] == {"mode": "per_account", "ban_after": 0, "source": "file"}
    assert fixed["notify"]["sound"] is True and fixed["notify"]["desktop"] is False


def test_normalize_keeps_good_values():
    kept = config.normalize({"captcha": {"mode": "pause", "pause_s": 30},
                             "proxy": {"mode": "sticky", "ban_after": 4, "source": "list"}})
    assert kept["captcha"]["mode"] == "pause" and kept["captcha"]["pause_s"] == 30
    assert kept["proxy"] == {"mode": "sticky", "ban_after": 4, "source": "list"}


def test_broken_file_falls_back_to_defaults(local_config):
    local_config.write_text("{ это не json", encoding="utf-8")
    assert config.load()["captcha"]["mode"] == config.DEFAULTS["captcha"]["mode"]
    local_config.write_text('"строка вместо объекта"', encoding="utf-8")
    assert config.load()["proxy"]["mode"] == "per_account"


def test_options_list_allowed_values():
    options = config.options()
    assert "wait" in options["captcha_modes"] and "per_account" in options["rotations"]
