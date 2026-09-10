"""Хранилище аккаунтов: запись, статусы, закрытие и битый файл.

В файле лежат пароли и ключи, а пишут в него несколько потоков запуска, поэтому
проверяем не только «данные сохранились», но и права на файл, поведение при
повреждённом JSON и то, что после `close()` ничего не теряется.
"""
import json
import os
import sys
import threading

from core.storage import AccountStore


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_add_saves_immediately_and_dedupes_by_key(tmp_path):
    path = tmp_path / "accounts.json"
    with AccountStore(path) as store:
        first = store.add("a@example.test", "demo", api_key="sk-1", password="secret")
        assert first["device_uuid"] and first["status"] == "active"
        assert read(path)[0]["email"] == "a@example.test"
        again = store.add("b@example.test", "demo", api_key="sk-1",
                          base_url="https://api.example.test", status="no_key")
        assert again["email"] == "a@example.test"  # тот же аккаунт, а не копия
        assert again["base_url"] == "https://api.example.test"
        assert len(store.all()) == 1
        store.add("c@example.test", "demo")
        assert {item["email"] for item in store.all()} == {"a@example.test", "c@example.test"}
        store.remove("a@example.test")
        assert [item["email"] for item in read(path)] == ["c@example.test"]


def test_status_is_written_on_close(tmp_path):
    """Статусы пишутся с задержкой, но `close()` дописывает их наверняка."""
    path = tmp_path / "accounts.json"
    store = AccountStore(path)
    store.add("a@example.test", "demo", status="registration_pending")
    store.set_status("a@example.test", "active")
    store.close()
    assert read(path)[0]["status"] == "active"
    assert store.active("demo")[0]["email"] == "a@example.test"
    assert store.active("другой") == []
    store.close()  # повторное закрытие безопасно


def test_reopen_reads_what_was_written(tmp_path):
    path = tmp_path / "accounts.json"
    with AccountStore(path) as store:
        store.add("a@example.test", "demo", password="secret")
    with AccountStore(path) as again:
        assert again.get("a@example.test")["password"] == "secret"
        assert again.get("нет@example.test") is None


def test_broken_file_is_moved_aside(tmp_path, capsys):
    path = tmp_path / "accounts.json"
    path.write_text("{это не список}", encoding="utf-8")
    with AccountStore(path) as store:
        assert store.all() == []
        store.add("a@example.test", "demo")
    assert (tmp_path / "accounts.json.corrupt").exists()
    assert "повреждён" in capsys.readouterr().out
    assert [item["email"] for item in read(path)] == ["a@example.test"]


def test_json_object_instead_of_list_is_rejected(tmp_path):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps({"email": "a@example.test"}), encoding="utf-8")
    with AccountStore(path) as store:
        assert store.all() == []


def test_records_without_email_are_skipped(tmp_path):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps([{"provider": "demo"}, "мусор",
                                {"email": "a@example.test"}]), encoding="utf-8")
    with AccountStore(path) as store:
        assert len(store.all()) == 2          # строка отброшена, объекты остались
        assert store.get("a@example.test") is not None
        assert store.get("") is None          # запись без почты не путает поиск


def test_file_is_private_to_owner(tmp_path):
    if sys.platform.startswith("win"):
        return
    path = tmp_path / "accounts.json"
    with AccountStore(path) as store:
        store.add("a@example.test", "demo", password="secret")
    assert os.stat(path).st_mode & 0o077 == 0


def test_parallel_stores_do_not_clash(tmp_path):
    """Две копии хранилища пишут в один файл — временные имена не совпадают."""
    path = tmp_path / "accounts.json"
    first, second = AccountStore(path), AccountStore(path)
    assert first._tmp != second._tmp
    try:
        threads = [threading.Thread(target=store.add, args=(f"{index}@example.test", "demo"))
                   for index, store in enumerate((first, second))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        first.close()
        second.close()
    assert not list(tmp_path.glob("*.tmp"))
    assert isinstance(read(path), list)
