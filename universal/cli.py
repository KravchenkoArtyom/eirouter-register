"""Интерактивный консольный запуск: `py -3 -m universal`.

Порядок вопросов: прокси -> почтовый сервис -> сценарий -> количество.
Прокси можно передать списком (`--proxy` несколько раз или файлом) и выбрать
правило ротации; задержки и поведение при капче задаются флагами.
Всё то же есть в WebUI (`scripts\\webui.bat`), консоль оставлена для запуска
без браузера и для автоматизации через аргументы.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx
from playwright.async_api import Error as PlaywrightError

from core.adspower import AdsPowerProfiles
from core.browser_profile import BrowserProfiles
from core.proxy_pool import ROTATIONS, ProxyRotation, load_list, normalize_proxy, redact_proxy
from core.run_logs import run_logged
from universal.captcha import MODES as CAPTCHA_MODES, CaptchaSettings, CaptchaSkipped, CaptchaStop
from universal.control import RunControl
from universal.mail import mail_service_ids, normalize_mail_service
from universal.pacing import Pacing
from universal.runner import register_one
from universal.scenarios import ROOT, SCENARIOS_DIR, load_scenarios, title_of

PROFILES_DIR = ROOT / "universal_profiles"


def prompt_count() -> int:
    while True:
        try:
            value = int(input("Сколько аккаунтов зарегистрировать? ").strip())
            if value > 0:
                return value
        except ValueError:
            pass
        print("Введите положительное целое число.", flush=True)


def prompt_choice(prompt: str, choices: dict[str, str]) -> str:
    while True:
        print(prompt, flush=True)
        for key, label in choices.items():
            print(f"  {key}. {label}", flush=True)
        selected = input("Выбор: ").strip() or next(iter(choices))
        if selected in choices:
            return selected
        print("Выберите один из вариантов.", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Универсальный регистратор аккаунтов")
    parser.add_argument("--scenario", type=Path, help="JSON сценария; иначе выбор в меню")
    parser.add_argument("--count", type=int, help="Количество аккаунтов; иначе спросим")
    parser.add_argument("--mail-service", help="Имя почтового сервиса: "
                        + " | ".join(mail_service_ids()) + " или свой из mail_services/")
    parser.add_argument("--proxy", action="append", default=[],
                        help="HTTP CONNECT прокси; флаг можно повторить")
    parser.add_argument("--proxies-file", type=Path, default=ROOT / "proxies.txt")
    parser.add_argument("--direct", action="store_true", help="Без прокси")
    parser.add_argument("--use-proxies", action="store_true", help="Взять --proxies-file без вопроса")
    parser.add_argument("--rotation", choices=ROTATIONS, default="per_account",
                        help="Правило смены прокси между аккаунтами")
    parser.add_argument("--ban-after", type=int, default=0,
                        help="Убрать прокси из очереди после N неудач подряд (0 — не убирать)")
    parser.add_argument("--captcha-mode", choices=CAPTCHA_MODES, default="wait",
                        help="Что делать при капче: ждать человека, пауза, пропуск, только лог")
    parser.add_argument("--captcha-timeout", type=float, default=300,
                        help="Сколько ждать решения капчи, с")
    parser.add_argument("--step-delay", type=float, nargs=2, metavar=("MIN_MS", "MAX_MS"),
                        help="Задержка перед шагом, мс")
    parser.add_argument("--account-pause", type=float, nargs=2, metavar=("MIN_S", "MAX_S"),
                        help="Пауза между аккаунтами, с")
    parser.add_argument("--browser", choices=("auto", "chrome", "adspower"), default="auto")
    parser.add_argument("--adspower-config", type=Path, default=ROOT / "adspower.local.json")
    parser.add_argument("--output", type=Path, default=ROOT / "universal_accounts.json")
    args = parser.parse_args()

    scenarios = load_scenarios()
    if not scenarios and not args.scenario:
        parser.error(f"В {SCENARIOS_DIR} нет сценариев")
    if args.direct:
        proxies = []
    elif args.proxy:
        proxies = [normalize_proxy(item) for item in args.proxy]
    elif args.use_proxies:
        proxies = load_list(args.proxies_file)
    else:
        proxies = load_list(args.proxies_file) if prompt_choice(
            "Использовать прокси?", {"1": "Нет", "2": f"Да ({args.proxies_file.name})"}) == "2" else []
    if args.mail_service:
        service = normalize_mail_service(args.mail_service)
        if service not in mail_service_ids():
            parser.error("--mail-service: " + " | ".join(mail_service_ids()))
    else:
        known = mail_service_ids()
        choice = prompt_choice("Почтовый сервис:", {
            str(number): name for number, name in enumerate(known, 1)})
        service = normalize_mail_service(known[int(choice) - 1])
    if args.scenario:
        scenario = json.loads(args.scenario.read_text(encoding="utf-8"))
    else:
        selected = prompt_choice("Сценарий сайта:", {
            str(index + 1): title_of(item["_path"], item)
            for index, item in enumerate(scenarios)})
        scenario = scenarios[int(selected) - 1]
    if not isinstance(scenario, dict) or not isinstance(scenario.get("url"), str):
        parser.error("Сценарий должен быть объектом с полем url")
    count = args.count or prompt_count()
    if count < 1:
        parser.error("--count должен быть положительным")

    config = json.loads(args.adspower_config.read_text(encoding="utf-8")) \
        if args.adspower_config.exists() else {}
    use_adspower = args.browser == "adspower" or (args.browser == "auto" and bool(config))
    if use_adspower:
        key = config.get("api_key")
        if not key:
            parser.error("В adspower.local.json нет api_key")
        profiles = AdsPowerProfiles(PROFILES_DIR, config.get("base_url", "http://127.0.0.1:50325"), key)
    else:
        profiles = BrowserProfiles(PROFILES_DIR)

    def log(message: str) -> None:
        print(message, flush=True)

    def listener(event: dict) -> None:
        # Капча требует человека: в консоли зовём звонком и рамкой.
        if event.get("type") == "captcha":
            print("\a" + "=" * 60, flush=True)
            print("  КАПЧА: решите проверку в открытом окне браузера", flush=True)
            print("=" * 60, flush=True)

    control = RunControl(log, listener)
    pacing = Pacing.from_dict({
        **({"step_min_ms": args.step_delay[0], "step_max_ms": args.step_delay[1]}
           if args.step_delay else {}),
        **({"account_min_s": args.account_pause[0], "account_max_s": args.account_pause[1]}
           if args.account_pause else {}),
    })
    captcha = CaptchaSettings.from_dict({"mode": args.captcha_mode,
                                         "timeout": args.captcha_timeout})
    rotation = ProxyRotation(proxies, args.rotation, args.ban_after)
    log(f"[universal] Сеть: {rotation.summary()}; капча: {captcha.mode}")

    failures = 0
    skipped = 0
    for index in range(count):
        if index:
            pause = pacing.account_seconds()
            if pause:
                log(f"[universal] Пауза между аккаунтами: {pause:.0f} с")
                time.sleep(pause)
        proxy = rotation.take()
        log(f"[universal] Аккаунт {index + 1}/{count}; "
            f"сценарий={scenario.get('name', 'unnamed')}; почта={service}; "
            f"сеть={redact_proxy(proxy) if proxy else 'напрямую'}")
        try:
            asyncio.run(register_one(scenario, service, profiles, args.output, proxy, log,
                                     control, pacing, captcha))
            rotation.report(proxy, True)
        except CaptchaStop:
            log("[universal] Остановлено на капче")
            break
        except CaptchaSkipped as error:
            skipped += 1
            rotation.report(proxy, False)
            log(f"[universal] Аккаунт {index + 1} пропущен: {error}")
        except (RuntimeError, TimeoutError, PlaywrightError, httpx.HTTPError, OSError, ValueError) as error:
            failures += 1
            rotation.report(proxy, False)
            log(f"[universal] Аккаунт {index + 1} не удался: {type(error).__name__}: {error}")
            log(f"[universal] Перехожу к следующему (неудач: {failures})")
    if failures or skipped:
        log(f"[universal] Готово, неудач: {failures}/{count}, пропущено: {skipped}; "
            "подробности в logs/")
        return 1
    return 0


def run() -> int:
    return run_logged(main, ROOT / "logs")


if __name__ == "__main__":
    sys.exit(run())
