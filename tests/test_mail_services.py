"""Свои почтовые сервисы: файлы описаний, валидация и исполнение шагов."""
import asyncio
import json

import pytest

from universal import mail_services as store
from universal.mail import create_mailbox, mail_service_ids
from universal.scripted_mail import ScriptedMailClient

TEMP_NAME = "pytest-temp-mail"

MAIL_PAGE = """
<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8"><title>Fake mail</title></head>
<body>
  <button id="new">Создать</button>
  <div id="form" style="display:none">
    <input id="user" placeholder="имя">
    <button id="create">Создайте</button>
  </div>
  <input id="address" readonly value="">
  <button id="refresh">Refresh</button>
  <ul id="list"></ul>
  <div id="body"></div>
<script>
  let refreshes = 0;
  document.getElementById('new').onclick = () => {
    document.getElementById('form').style.display = 'block';
  };
  document.getElementById('create').onclick = () => {
    document.getElementById('address').value =
      document.getElementById('user').value + '@fake.test';
  };
  document.getElementById('refresh').onclick = () => {
    refreshes += 1;
    if (refreshes >= 2 && !document.querySelector('.mail-item')) {
      const item = document.createElement('li');
      item.className = 'mail-item';
      item.textContent = 'Fake Site: подтверждение';
      item.onclick = () => {
        document.getElementById('body').innerHTML =
          '<p>Your code is 246813</p><a href="https://site.test/verify?token=abc">Подтвердить</a>'
          + '<a href="https://site.test/privacy">Privacy</a>';
      };
      document.getElementById('list').appendChild(item);
    }
  };
</script>
</body></html>
"""


def description(url):
    return {
        "name": "fake", "label": "Fake mail", "url": url, "poll_interval": 0.3,
        "address": {"source": "value", "selectors": ["#address"], "timeout": 10},
        "create": [{"action": "click", "selectors": ["#new"]},
                   {"action": "fill", "selectors": ["#user"], "value": "{username}"},
                   {"action": "click", "selectors": ["#create"]}],
        "refresh": [{"action": "click", "selectors": ["#refresh"]}],
        "open_message": [{"action": "click", "selectors": [".mail-item"], "timeout_ms": 700}],
        "code": {"length": 6, "keywords": "code, код"},
        "link": {"keywords": "verify", "exclude": "privacy"},
    }


# ---- файлы описаний ----
def test_builtin_services_are_known():
    assert set(mail_service_ids()) >= {"none", "tmail", "mail.tm"}
    assert store.normalize("WibuCrypto") == "tmail"
    assert store.is_known("mail.tm") and not store.is_known("нет-такого")


def test_lifecycle_create_validate_delete():
    try:
        store.delete(TEMP_NAME)
    except KeyError:
        pass
    path, data = store.create(TEMP_NAME, "https://mail.test/inbox", "button")
    try:
        assert path.name == f"{TEMP_NAME}.json"
        assert data["create"] and data["address"]["source"] == "value"
        assert TEMP_NAME in store.custom_ids()
        assert store.is_known(TEMP_NAME)
        assert store.validate(data) == []

        with pytest.raises(FileExistsError):
            store.create(TEMP_NAME, "https://mail.test/inbox")

        found = store.get(TEMP_NAME)
        assert found["url"] == "https://mail.test/inbox"
        summary = {item["id"]: item for item in store.summaries()}
        assert summary[TEMP_NAME]["kind"] == "custom" and summary[TEMP_NAME]["steps"] >= 3
    finally:
        target = store.delete(TEMP_NAME)
        target.unlink(missing_ok=True)
    assert TEMP_NAME not in store.custom_ids()


def test_builtin_name_is_protected():
    with pytest.raises(ValueError):
        store.create("tmail", "https://mail.test")
    assert any("встроенный" in error for error in store.validate(
        {"name": "tmail", "url": "https://mail.test"}))


def test_validation_messages():
    assert store.validate("нет") == ["Корень должен быть JSON-объектом"]
    errors = store.validate({"name": "", "url": "mail.test"})
    assert any("name" in error for error in errors)
    assert any("url" in error for error in errors)
    errors = store.validate({"name": "x", "url": "https://mail.test",
                             "address": {"source": "attribute", "selectors": ["#a"]}})
    assert any("attribute" in error for error in errors)
    errors = store.validate({"name": "x", "url": "https://mail.test",
                             "address": {"source": "value", "selectors": []}})
    assert any("selectors" in error for error in errors)
    # почтовые действия внутри почтового сервиса запрещены: ящика ещё нет
    errors = store.validate({"name": "x", "url": "https://mail.test",
                             "address": {"source": "page"},
                             "create": [{"action": "mail_wait_code"}]})
    assert any("недоступно" in error for error in errors)
    errors = store.validate({"name": "x", "url": "https://mail.test",
                             "address": {"source": "page"}, "code": {"length": 99}})
    assert any("code.length" in error for error in errors)


def test_templates_are_valid_descriptions():
    for key in store.TEMPLATES:
        data = json.loads(json.dumps(store.TEMPLATES[key]["data"]))
        data.update({"name": "probe", "url": "https://mail.test"})
        data.setdefault("address", {"source": "page"})
        assert store.validate(data) == [], key


# ---- исполнение шагов ----
@pytest.fixture(scope="module")
def loop():
    instance = asyncio.new_event_loop()
    yield instance
    instance.close()


@pytest.fixture(scope="module")
def context(loop, tmp_path_factory):
    from playwright.async_api import async_playwright
    playwright = loop.run_until_complete(async_playwright().start())
    browser = loop.run_until_complete(playwright.chromium.launch())
    ctx = loop.run_until_complete(browser.new_context())
    yield ctx
    loop.run_until_complete(ctx.close())
    loop.run_until_complete(browser.close())
    loop.run_until_complete(playwright.stop())


@pytest.fixture(scope="module")
def mail_url(tmp_path_factory):
    path = tmp_path_factory.mktemp("mail") / "inbox.html"
    path.write_text(MAIL_PAGE, encoding="utf-8")
    return path.as_uri()


def test_scripted_client_creates_mailbox_and_reads_letter(loop, context, mail_url):
    client = ScriptedMailClient(description(mail_url), lambda message: None)
    loop.run_until_complete(client.open(context))
    address = loop.run_until_complete(client.create_mailbox("tester"))
    assert address == "tester@fake.test"

    code = loop.run_until_complete(client.wait_for_code(length=6, timeout=20))
    assert code == "246813"

    link = loop.run_until_complete(client.wait_for_link(timeout=20))
    assert link == "https://site.test/verify?token=abc"
    loop.run_until_complete(client.page.close())


def test_scripted_client_reports_missing_address(loop, context, mail_url):
    broken = description(mail_url)
    broken["address"] = {"source": "value", "selectors": ["#nothing"], "timeout": 1}
    broken["create"] = []
    client = ScriptedMailClient(broken, lambda message: None)
    loop.run_until_complete(client.open(context))
    with pytest.raises(TimeoutError):
        loop.run_until_complete(client.create_mailbox("tester"))
    loop.run_until_complete(client.page.close())


def test_create_mailbox_uses_custom_service(loop, context, mail_url):
    path, data = store.create(TEMP_NAME, mail_url, "blank")
    try:
        data.update(description(mail_url))
        data["name"] = TEMP_NAME
        store.save(path, data)
        mailbox = loop.run_until_complete(
            create_mailbox(TEMP_NAME, context, None, lambda message: None, "leon"))
        assert isinstance(mailbox, ScriptedMailClient)
        assert mailbox.address == "leon@fake.test"
        loop.run_until_complete(mailbox.page.close())
    finally:
        store.delete(TEMP_NAME).unlink(missing_ok=True)


def test_unknown_service_is_reported(loop, context):
    with pytest.raises(ValueError):
        loop.run_until_complete(
            create_mailbox("no-such-mail", context, None, lambda message: None))
