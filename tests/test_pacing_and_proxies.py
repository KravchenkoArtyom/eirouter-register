"""Задержки запуска и очередь прокси: границы, ротация, выключение адреса."""
import asyncio
import time

import pytest

from core.proxy_pool import (ProxyRotation, load_list, normalize_proxy, parse_lines,
                             redact_proxy, save_list)
from universal.pacing import DEFAULTS, Pacing

PROXIES = ["http://1.1.1.1:8000", "http://user:pass@2.2.2.2:8001", "http://3.3.3.3:8002"]


# ---- задержки ----
def test_pacing_defaults_and_from_dict():
    default = Pacing()
    assert default.step_min_ms == DEFAULTS["step_min_ms"]
    custom = Pacing.from_dict({"step_min_ms": 10, "step_max_ms": 20, "account_min_s": 1,
                               "account_max_s": 2})
    assert (custom.step_min_ms, custom.step_max_ms) == (10, 20)
    assert 1 <= custom.account_seconds() <= 2
    # мусор и отрицательные значения не должны ломать запуск
    broken = Pacing.from_dict({"step_min_ms": "быстро", "step_max_ms": -5})
    assert broken.step_min_ms == DEFAULTS["step_min_ms"] and broken.step_max_ms == 0


def test_pacing_step_seconds_stays_in_range():
    pacing = Pacing(step_min_ms=100, step_max_ms=200, click_extra_ms=50)
    for _ in range(20):
        plain = pacing.step_seconds("fill")
        clicked = pacing.step_seconds("click")
        assert 0.1 <= plain <= 0.2
        assert 0.15 <= clicked <= 0.25


def test_pacing_zero_means_no_wait():
    pacing = Pacing(step_min_ms=0, step_max_ms=0, account_min_s=0, account_max_s=0)
    assert pacing.step_seconds("click") == 0
    assert pacing.account_seconds() == 0
    started = time.monotonic()
    asyncio.run(pacing.before_step("click"))
    assert time.monotonic() - started < 0.2


def test_pacing_round_trip_dict():
    pacing = Pacing.from_dict({"after_captcha_s": 7})
    assert Pacing.from_dict(pacing.as_dict()).after_captcha_s == 7


# ---- разбор списка ----
def test_parse_lines_reports_bad_rows():
    proxies, errors = parse_lines("1.1.1.1:8000\n# комментарий\n\nне прокси\n1.1.1.1:8000")
    assert proxies == ["http://1.1.1.1:8000"]
    assert len(errors) == 1 and "строка 4" in errors[0]


def test_save_list_writes_file(tmp_path):
    target = tmp_path / "proxies.txt"
    assert save_list(target, PROXIES + PROXIES) == 3
    assert load_list(target) == PROXIES
    assert save_list(target, []) == 0
    assert load_list(target) == []


def test_redact_hides_credentials():
    assert redact_proxy("http://user:pass@2.2.2.2:8001") == "http://2.2.2.2:8001"
    with pytest.raises(ValueError):
        normalize_proxy("socks5://1.1.1.1:1080")


def test_socks_line_gets_its_own_explanation():
    """Про socks говорим прямо: браузер такие адреса не примет."""
    items, errors = parse_lines("socks5://1.1.1.1:1080\nмусор")
    assert items == []
    assert "socks" in errors[0] and "http" in errors[0]
    assert "не похоже на прокси" in errors[1]


# ---- ротация ----
def test_rotation_per_account_goes_round():
    rotation = ProxyRotation(PROXIES, "per_account")
    taken = [rotation.take() for _ in range(4)]
    assert taken == PROXIES + PROXIES[:1]
    assert "3 прокси" in rotation.summary()


def test_rotation_sticky_keeps_one_until_error():
    rotation = ProxyRotation(PROXIES, "sticky")
    first = rotation.take()
    assert rotation.take() == first
    rotation.report(first, False)
    assert rotation.take() != first


def test_rotation_on_error_switches_only_after_failure():
    rotation = ProxyRotation(PROXIES, "on_error")
    first = rotation.take()
    rotation.report(first, True)
    assert rotation.take() == first
    rotation.report(first, False)
    assert rotation.take() != first


def test_rotation_random_stays_inside_pool():
    rotation = ProxyRotation(PROXIES, "random")
    assert all(rotation.take() in PROXIES for _ in range(10))


def test_rotation_bans_after_repeated_failures():
    rotation = ProxyRotation(PROXIES[:2], "per_account", ban_after=2)
    bad = rotation.take()
    rotation.report(bad, False)
    rotation.report(bad, False)
    assert bad not in rotation.alive
    assert all(rotation.take() != bad for _ in range(5))
    described = {item["proxy"]: item for item in rotation.describe()}
    assert described[redact_proxy(bad)]["banned"] is True


def test_rotation_without_proxies_means_direct():
    rotation = ProxyRotation([], "per_account")
    assert rotation.take() is None
    assert "напрямую" in rotation.summary()
    rotation.report(None, False)  # не должно падать


def test_rotation_survives_full_ban():
    rotation = ProxyRotation(PROXIES[:1], "per_account", ban_after=1)
    only = rotation.take()
    rotation.report(only, False)
    assert rotation.take() is None
