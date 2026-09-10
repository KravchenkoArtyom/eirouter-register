# Структура проекта

```
universal-autoregister/
├─ scripts/              Windows-лаунчеры (.bat) — точки входа для человека
├─ webui/                панель в браузере: FastAPI + статика
│  ├─ server.py          сборка приложения (роутеры + статика)
│  ├─ settings.py        пути и константы
│  ├─ config.py          настройки панели (`webui.local.json`)
│  ├─ security.py        страж по `Host`, токен доступа, защитные заголовки
│  ├─ inspect_session.py страница сайта в headless-браузере, зеркало DOM
│  ├─ element_buffer.py  временное хранилище снятых элементов
│  ├─ api/               HTTP-эндпоинты: scenarios, runs, mail, proxies,
│  │                     settings, accounts, inspector, buffer
│  └─ static/            index.html, styles.css, how-it-works.svg, picker.js,
│                        js/ — модули разделов панели
├─ universal/            движок универсальной регистрации
│  ├─ actions.py         каталог действий шага (единый для раннера и панели)
│  ├─ runner.py          исполнение сценария в браузере
│  ├─ mail.py            почтовые сервисы и почтовые действия
│  ├─ mail_services.py   реестр почтовых сервисов (встроенные + свои)
│  ├─ scripted_mail.py   свой почтовый сервис: ящик и письма по шагам
│  ├─ mail_text.py       разбор письма: код, ссылка, адрес
│  ├─ captcha.py         поиск капчи и режимы ожидания
│  ├─ pacing.py          задержки между шагами и аккаунтами
│  ├─ control.py         связь запуска с человеком (события и ответы)
│  ├─ step_check.py      общая проверка шагов
│  ├─ scenarios.py       файлы сценариев: чтение, запись, валидация
│  └─ cli.py             интерактивный запуск в консоли
├─ providers/            регистраторы конкретных сервисов на NewAPI
│                        (eirouter, wisdomsatan) — там же захват ключа
├─ core/                 инфраструктура: браузерные профили, AdsPower, прокси,
│                        хранилище аккаунтов, логи, tmail-клиент
├─ recipes/              рецепты шагов для провайдеров
├─ universal_scenarios/  сценарии сайтов (JSON), `_trash/` — удалённые
├─ mail_services/        свои почтовые сервисы (JSON), `_trash/` — удалённые
├─ tools/                консольные инструменты (редактор сценариев,
│                        синхронизация схемы `make_diagram.py`)
├─ tests/                тесты (pytest)
├─ logs/                 не в репозитории: `runs/` — логи и итоги запусков,
│                        `failures/` — снимки упавших аккаунтов
├─ .github/workflows/    проверка кода и тесты на каждый push
└─ docs/                 документация, `images/` — картинки
```

В корне лежат `pyproject.toml` (версия, правила `ruff`, настройки `pytest`),
`requirements.txt` и `requirements-dev.txt`, `LICENSE` (MIT).

## Точки входа

| Что нужно | Команда |
| --- | --- |
| Панель в браузере | `scripts\webui.bat` или `py -3 -m webui` |
| Регистрация в консоли | `scripts\universal_autoregister.bat` или `py -3 -m universal` |
| Правка сценария в консоли | `scripts\edit_universal_scenario.bat` или `py -3 -m tools.scenario_editor` |
| Регистратор eirouter | `scripts\register_eirouter.bat` или `py -3 -m providers.eirouter --interactive` |
| Регистратор wisdomsatan | `scripts\register_wisdomsatan.bat` |
| Обновиться из GitHub | `scripts\sync.bat` |
| Тесты | `py -3 -m pytest` |
| Проверка кода | `py -3 -m ruff check .` |

## Что переехало

| Было | Стало |
| --- | --- |
| `webui.py`, `webui.html` | `webui/` (сервер, API, статика) |
| `universal_autoregister.py` | `universal/` (`runner.py`, `cli.py`, `mail.py`, `scenarios.py`, `actions.py`) |
| `universal_scenario_editor.py` | `tools/scenario_editor.py` |
| `universal_autoregister_gui.py` | удалён: панель (`webui/`) умеет всё то же |
| `*.bat` в корне | `scripts/*.bat` |
| `core/test_*.py` | `tests/` |
| `README.md` (про eirouter), `UNIVERSAL_AUTOREGISTER.md` | `docs/` |

Данные и секреты остались на месте и по-прежнему не попадают в репозиторий:
`universal_accounts.json`, `eirouter_accounts.json`, `proxies.txt`,
`adspower.local.json`, `webui.local.json`, `profiles/`,
`universal_profiles/`, `logs/`, `webui_elements.json`. Файл аккаунтов и
`proxies.txt` создаются с правами «только владелец».
