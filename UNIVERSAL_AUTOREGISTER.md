# Universal registrar

Run `universal_autoregister.bat`. The interactive flow asks, in order:

1. whether to use `proxies.txt`;
2. `tmail.wibucrypto.pro` or `Mail.tm`;
3. a site scenario from `universal_scenarios`;
4. the account count.

For a visual interface run `webui.bat` (http://127.0.0.1:8765). The WebUI
replaces the old Tkinter `universal_autoregister_gui.py`: scenario picker,
run settings (count, mail service, proxies, browser), live log streaming,
scenario step editor with validation, and an account viewer — all in the
browser, no desktop GUI needed. The registrar runs in-process in a background
thread instead of a child process. Requires `fastapi` + `uvicorn`
(see `requirements.txt`).

The legacy Tkinter GUI (`universal_autoregister_gui.bat`) is kept as-is.

## Inspector (element picker)

The editor has an "Inspector" card: enter a URL, the server opens it in
headless Chromium and shows a screenshot. Clicking an element resolves it
via elementFromPoint into selector candidates (id, test attributes, text,
CSS path) with a uniqueness check and a suggested action (fill/click/check/
select). "Highlight" verifies the selector with a red outline; "To step" /
"As new step" puts it into the edited scenario. Endpoints:
`POST /api/inspect/start`, `GET /api/inspect/{sid}/shot`,
`POST /api/inspect/{sid}/pick`, `POST /api/inspect/{sid}/highlight`,
`DELETE /api/inspect/{sid}`.

## Mail stages in the scenario

`wibucrypto` is an alias of the browser-based `tmail` client
(`normalize_mail_service`), accepted by the CLI and the WebUI. A scenario
may carry a `mail` hint (none|tmail|wibucrypto|mail.tm); the WebUI run form
defaults to "auto" and resolves the service from the scenario. The
`verification` object (`send`/`wait`/`complete`) is editable in the editor
under the "verification" tab alongside steps/submit/success, including
`wait_email_code` (length/timeout). Validation covers nested verification
steps and the `mail` field.

Each site is a JSON file. Copy `universal_scenarios/example_email.json` and
change its `url`, selectors, and steps. Every step can use `selector` or an
ordered `selectors` fallback list. Values can contain `{email}`, `{login}`,
`{username}`, `{password}`, and `{password_confirm}`.

Run `edit_universal_scenario.bat` for a guided editor. It can add, remove, or
change steps and insert HTML from a file. The same operations are available
without the menu, for example:

```powershell
py -3 universal_scenario_editor.py --scenario universal_scenarios/my-site.json --remove-step 3
py -3 universal_scenario_editor.py --scenario universal_scenarios/my-site.json --add-dom ".banner" remove
py -3 universal_scenario_editor.py --scenario universal_scenarios/my-site.json --add-dom "#form" append_html --html-file fragment.html
```

Supported actions are `fill`, `type`, `check`, `uncheck`, `click`, `select`,
`press`, `wait`, `wait_visible`, `wait_url`, `wait_email_code`, and `dom`.
The `dom` action supports `remove`, `set_attribute`, and `set_html`, which
allows a scenario to adapt to small layout changes without editing Python.
The `insert_html`, `append_html`, `prepend_html`, and `set_text` operations are
also available. These operations change the page DOM for the current run; they
do not change the remote site.

Email verification is optional. Add a `verification` object with `send`,
`wait`, and `complete` steps when a site emails a code. The runner uses a
separate browser profile and saves credentials to `universal_accounts.json`.
Add a `success` list when the site has a reliable post-submit URL or element;
the account is saved only after those steps complete.

The runner does not bypass CAPTCHA or other access controls. If a site presents
one, complete it in the visible browser and let the scenario continue.
