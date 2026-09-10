"""Инспектор без браузера: сборка зеркала и временный буфер элементов."""
from webui.element_buffer import ElementBuffer
from webui.inspect_session import MIRROR_JS, PICK_JS, inject_picker
from webui.settings import STATIC_DIR


def test_injects_script_before_body_close():
    result = inject_picker("<html><body><p>x</p></body></html>", "console.log(1)", "a1", 2)
    assert result.index("console.log(1)") < result.index("</body>")
    assert 'data-sid="a1"' in result and 'data-revision="2"' in result


def test_injects_script_when_body_is_missing():
    assert inject_picker("<p>x</p>", "console.log(1)").endswith("</script>")


def test_picker_script_is_shipped_and_inlined():
    script = (STATIC_DIR / "picker.js").read_text(encoding="utf-8")
    # Ссылку использовать нельзя: в зеркале стоит <base href> сайта.
    assert "</script>" not in script
    assert "webui-picker" in script
    assert inject_picker("<body></body>", script).count("webui-picker") >= 1


def test_mirror_script_cleans_page_and_sets_base():
    for fragment in ("cloneNode", "script, noscript", "document.baseURI", "iframe"):
        assert fragment in MIRROR_JS
    assert "elementFromPoint" in PICK_JS


def test_element_buffer_survives_reload(tmp_path):
    path = tmp_path / "elements.json"
    buffer = ElementBuffer(path)
    assert buffer.all() == []
    item = buffer.add({"selector": "#email", "action": "fill", "value": "{email}",
                       "ignored": "не сохраняем"})
    assert item["id"] and "ignored" not in item
    assert buffer.update(item["id"], {"note": "почта"})["note"] == "почта"
    assert buffer.update("missing", {"note": "x"}) is None

    reopened = ElementBuffer(path)
    assert [entry["selector"] for entry in reopened.all()] == ["#email"]
    assert reopened.remove(item["id"]) is True
    assert reopened.remove(item["id"]) is False
    reopened.add({"selector": "#password"})
    assert reopened.clear() == 1
    assert ElementBuffer(path).all() == []


def test_element_buffer_ignores_broken_file(tmp_path):
    path = tmp_path / "elements.json"
    path.write_text("{ не json", encoding="utf-8")
    assert ElementBuffer(path).all() == []
