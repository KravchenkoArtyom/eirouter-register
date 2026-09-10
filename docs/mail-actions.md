# Почтовые действия в шагах

Почта больше не привязана к одному месту сценария: любое почтовое действие
можно поставить в любую секцию (`steps`, `verification.*`, `submit`, `success`)
и в любую позицию, поменять его поля или удалить — в панели («Сценарий» →
группа «Почта») или прямо в JSON.

| Действие | Поля | Результат |
| --- | --- | --- |
| `mail_create` | `service` (пусто — как в запуске; `tmail`, `mail.tm` или свой сервис), `username` | создаёт ящик и обновляет `{email}` |
| `mail_wait_code` | `length`, `timeout`, `sender_hint`, `var` | код письма в `{code}` (или в `{var}`) |
| `mail_wait_link` | `timeout`, `keywords`, `var` | ссылка подтверждения в `{link}` |
| `mail_open_link` | `target` (`same_tab`/`new_tab`), `close_after`, `timeout`, `keywords`, `var` | открывает ссылку из письма |
| `mail_refresh` | — | для браузерных сервисов жмёт «Обновить», для Mail.tm ничего не делает |

`wait_email_code` — прежнее имя `mail_wait_code`, старые сценарии работают
без правок.

Список сервисов в поле `service` собирается из встроенных и своих: свой
добавляется в разделе «Почта» и описывается шагами —
[mail-services.md](mail-services.md).

## Как выбирается ящик

- Если в сценарии **нет** `mail_create`, ящик создаётся до открытия сайта, как
  раньше: сервис берётся из формы запуска, а в режиме «авто» — из поля `mail`
  сценария.
- Если `mail_create` **есть**, ящиком управляет сценарий: можно создать его
  посреди формы или пересоздать (например, после отказа сайта) — адрес в
  `{email}` обновится, и аккаунт сохранится под финальным адресом.
- Все сервисы умеют и код, и ссылку: у `tmail` это браузерный клиент
  (`core/tmail_web.py`), у `mail.tm` — API (`providers/eirouter_mail.py`),
  у своих — шаги из `mail_services/*.json` (`universal/scripted_mail.py`).

## Примеры

Код после отправки формы:

```json
{"action": "click", "selectors": ["button:has-text('Send code')"]},
{"action": "mail_wait_code", "length": 6, "timeout": 180},
{"action": "fill", "selectors": ["input[name=code]"], "value": "{code}"}
```

Подтверждение по ссылке после отправки (секция `success`):

```json
{"action": "mail_open_link", "target": "same_tab", "keywords": "verify, confirm"},
{"action": "wait_url", "url": "**/welcome**"}
```

Отдельный ящик посреди сценария:

```json
{"action": "mail_create", "service": "mail.tm"},
{"action": "fill", "selectors": ["#email"], "value": "{email}"}
```
