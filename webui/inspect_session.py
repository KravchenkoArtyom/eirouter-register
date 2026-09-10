"""Сессия инспектора: живая страница сайта в headless-браузере на этой машине.

Идея: страницу сайта не пересказываем скриншотом, а показываем «зеркало» —
сериализованный DOM реальной страницы, отданный с нашего origin. Зеркало
открывается в iframe WebUI, поэтому подсветка при наведении и выбор элемента
работают мгновенно и без запросов к серверу (см. `static/picker.js`).

Скрипты сайта из зеркала вырезаны вместе с inline-обработчиками (`onclick`,
`onerror`), `javascript:`-ссылками и `srcdoc`; внешние фреймы не подгружаются,
`<base>` подставлен — стили и картинки берутся с сайта, а клики никуда не
ведут. Сверху панель отдаёт зеркало с CSP (разрешён только наш скрипт по
nonce) и держит его в песочнице iframe, поэтому даже пропущенный обработчик не
доберётся до локального API. Проверка селектора (`verify`) и действия (`act`)
выполняются уже на настоящей странице, поэтому селектор проверяется в том же
контексте, где потом поедет сценарий.
"""
from __future__ import annotations

import html as html_escape
import queue
import secrets
import threading
import time
from typing import Any

MIRROR_JS = """() => {
  const html = document.documentElement.cloneNode(true);
  html.querySelectorAll('script, noscript, link[as="script"], meta[http-equiv="Content-Security-Policy"], meta[http-equiv="refresh"]').forEach(node => node.remove());
  html.querySelectorAll('link[rel~="import" i], link[rel~="preload" i], link[rel~="prefetch" i]').forEach(node => node.remove());
  const live = document.querySelectorAll('input, textarea, select');
  const copy = html.querySelectorAll('input, textarea, select');
  for (let i = 0; i < live.length && i < copy.length; i++) {
    const source = live[i], target = copy[i];
    if (source.tagName === 'SELECT') {
      Array.from(target.options || []).forEach((option, index) => {
        if (index === source.selectedIndex) option.setAttribute('selected', 'selected');
        else option.removeAttribute('selected');
      });
    } else if (source.type === 'checkbox' || source.type === 'radio') {
      if (source.checked) target.setAttribute('checked', 'checked');
      else target.removeAttribute('checked');
    } else if (typeof source.value === 'string' && source.value) {
      target.setAttribute('value', source.value);
    }
  }
  html.querySelectorAll('iframe, frame, object, embed').forEach(node => {
    const src = node.getAttribute('src');
    if (src) { node.setAttribute('data-mirror-src', src); node.removeAttribute('src'); }
  });
  // Обработчики и javascript:-переходы сайта в зеркале не нужны: элементы
  // выбираются, а не выполняются.
  const dangerous = /^\\s*javascript:/i;
  html.querySelectorAll('*').forEach(node => {
    Array.from(node.attributes || []).forEach(attribute => {
      const name = attribute.name.toLowerCase();
      if (name.startsWith('on') || name === 'srcdoc' || name === 'nonce') {
        node.removeAttribute(attribute.name);
        return;
      }
      if (['href', 'src', 'action', 'formaction', 'xlink:href', 'data', 'poster'].indexOf(name) >= 0
          && dangerous.test(attribute.value || '')) {
        node.setAttribute('data-mirror-' + name, attribute.value);
        node.removeAttribute(attribute.name);
      }
    });
  });
  let head = html.querySelector('head');
  if (!head) { head = document.createElement('head'); html.insertBefore(head, html.firstChild); }
  head.querySelectorAll('base').forEach(node => node.remove());
  const base = document.createElement('base');
  base.setAttribute('href', document.baseURI);
  base.setAttribute('target', '_self');
  head.insertBefore(base, head.firstChild);
  return '<!DOCTYPE html>' + html.outerHTML;
}"""

# Клик по скриншоту (резервный режим): элемент под точкой + кандидаты селекторов.
PICK_JS = """(point) => {
  const element = document.elementFromPoint(point[0], point[1]);
  if (!element || !(element instanceof Element)) return null;
  const candidates = [];
  const push = value => { if (value && !candidates.includes(value)) candidates.push(value); };
  if (element.id) push('#' + CSS.escape(element.id));
  for (const name of ['data-testid', 'data-test', 'data-qa', 'name', 'aria-label', 'placeholder']) {
    const value = element.getAttribute ? element.getAttribute(name) : null;
    if (value) push(element.tagName.toLowerCase() + '[' + name + '="' + String(value).replace(/"/g, '') + '"]');
  }
  const text = (element.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 40);
  if (text && /^(button|a)$/i.test(element.tagName)) {
    push(element.tagName.toLowerCase() + ':has-text("' + text.replace(/"/g, '') + '")');
  }
  let node = element;
  const path = [];
  while (node && node.nodeType === 1 && path.length < 6) {
    let name = node.tagName.toLowerCase();
    const parent = node.parentElement;
    if (parent) {
      const same = Array.from(parent.children).filter(child => child.tagName === node.tagName);
      if (same.length > 1) name += ':nth-of-type(' + (same.indexOf(node) + 1) + ')';
    }
    path.unshift(name);
    node = parent;
  }
  push(path.join(' > '));
  const checked = candidates.map(value => {
    try { return {sel: value, unique: document.querySelectorAll(value).length === 1}; }
    catch (error) { return {sel: value, unique: false}; }
  });
  const box = element.getBoundingClientRect();
  const type = (element.getAttribute('type') || '').toLowerCase();
  let suggested = 'click';
  if (/^(input|textarea)$/i.test(element.tagName) && ['checkbox', 'radio', 'submit', 'button'].indexOf(type) < 0) suggested = 'fill';
  else if (type === 'checkbox') suggested = 'check';
  else if (/^select$/i.test(element.tagName)) suggested = 'select';
  return {tag: element.tagName.toLowerCase(), text: text,
          rect: {x: box.x, y: box.y, w: box.width, h: box.height},
          candidates: checked, suggested: suggested};
}"""

OUTLINE_JS = """(selector) => {
  const previous = document.getElementById('__webui_outline');
  if (previous) previous.remove();
  const element = document.querySelector(selector);
  if (!element) return 'not-found';
  const box = element.getBoundingClientRect();
  const marker = document.createElement('div');
  marker.id = '__webui_outline';
  marker.style.cssText = 'position:fixed;left:' + box.x + 'px;top:' + box.y + 'px;width:'
    + box.width + 'px;height:' + box.height + 'px;outline:2px solid #444;'
    + 'background:rgba(80,80,80,.12);z-index:2147483647;pointer-events:none;';
  document.body.appendChild(marker);
  return 'ok';
}"""

CLEAR_OUTLINE_JS = "() => { const node = document.getElementById('__webui_outline'); if (node) node.remove(); }"


class InspectSession:
    """Одна страница Playwright в своём потоке (sync API не дружит с asyncio)."""

    VIEWPORT = {"width": 1440, "height": 900}

    def __init__(self, url: str) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._error: Exception | None = None
        self.revision = 1
        self.url = url
        self.opened_at = time.monotonic()
        self.last_used = self.opened_at
        self.closed = False
        threading.Thread(target=self._worker, args=(url,), daemon=True,
                         name="webui-inspect").start()
        if not self._ready.wait(timeout=90):
            raise TimeoutError("браузер инспектора не стартовал")
        if self._error is not None:
            raise self._error

    # ---- поток браузера ----
    def _worker(self, url: str) -> None:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                try:
                    browser = pw.chromium.launch(headless=True)
                except Exception:
                    browser = pw.chromium.launch(headless=True, channel="chrome")
                page = browser.new_page(viewport=dict(self.VIEWPORT))
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                self._ready.set()
                while True:
                    task, box = self._queue.get()
                    if task is None:
                        break
                    try:
                        box["ret"] = task(page)
                    except Exception as error:
                        box["err"] = error
                    box["done"].set()
                browser.close()
        except Exception as error:
            self._error = error
            self._ready.set()

    def call(self, task, timeout: float = 90.0):
        if self.closed:
            raise RuntimeError("сессия инспектора закрыта")
        self.last_used = time.monotonic()
        box: dict[str, Any] = {"done": threading.Event()}
        self._queue.put((task, box))
        if not box["done"].wait(timeout):
            raise TimeoutError("инспектор не ответил")
        if "err" in box:
            raise box["err"]
        return box.get("ret")

    def close(self) -> None:
        self.closed = True
        self._queue.put((None, None))

    @property
    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_used

    # ---- операции ----
    def state(self) -> dict[str, Any]:
        data = self.call(lambda page: {"url": page.url, "title": page.title()})
        data["revision"] = self.revision
        data["viewport"] = dict(self.VIEWPORT)
        return data

    def mirror(self) -> str:
        return self.call(lambda page: page.evaluate(MIRROR_JS))

    def screenshot(self, selector: str | None = None) -> bytes:
        def _shot(page):
            if selector:
                page.evaluate(OUTLINE_JS, selector)
            png = page.screenshot()
            if selector:
                page.evaluate(CLEAR_OUTLINE_JS)
            return png
        return self.call(_shot)

    def pick(self, x: float, y: float) -> dict[str, Any] | None:
        return self.call(lambda page: page.evaluate(PICK_JS, [x, y]))

    def verify(self, selector: str) -> dict[str, Any]:
        """Проверить селектор на настоящей странице, а не в зеркале."""
        def _check(page):
            locator = page.locator(selector)
            count = locator.count()
            result: dict[str, Any] = {"count": count, "unique": count == 1,
                                      "visible": False, "tag": "", "text": ""}
            if count:
                first = locator.first
                try:
                    result["visible"] = first.is_visible()
                    result["tag"] = first.evaluate("node => node.tagName.toLowerCase()")
                    result["text"] = (first.inner_text() or "").strip()[:80]
                except Exception:
                    pass
            return result
        return self.call(_check)

    def act(self, action: str, selector: str = "", value: str = "",
            timeout_ms: int = 10_000) -> dict[str, Any]:
        """Продвинуть настоящую страницу: клик, ввод, скролл, переход, назад."""
        def _act(page):
            if action == "goto":
                page.goto(value, wait_until="domcontentloaded", timeout=60_000)
            elif action == "reload":
                page.reload(wait_until="domcontentloaded", timeout=60_000)
            elif action == "back":
                page.go_back(wait_until="domcontentloaded", timeout=60_000)
            elif action == "scroll":
                page.evaluate("offset => window.scrollBy(0, offset)", float(value or 600))
            else:
                target = page.locator(selector).first
                target.wait_for(state="visible", timeout=timeout_ms)
                if action == "click":
                    target.click()
                elif action == "fill":
                    target.fill(value or "")
                elif action == "type":
                    target.press_sequentially(value or "", delay=30)
                elif action == "check":
                    target.check()
                elif action == "uncheck":
                    target.uncheck()
                elif action == "press":
                    target.press(value or "Enter")
                elif action == "select":
                    target.select_option(value or "")
                else:
                    raise ValueError(f"Неподдерживаемое действие инспектора: {action}")
            page.wait_for_timeout(400)
            return {"url": page.url, "title": page.title()}
        result = self.call(_act)
        self.revision += 1
        result["revision"] = self.revision
        return result


def new_nonce() -> str:
    """Одноразовый nonce для CSP зеркала."""
    return secrets.token_urlsafe(12)


def mirror_csp(nonce: str) -> str:
    """CSP зеркала: наш скрипт по nonce, всё остальное — только показать."""
    return ("default-src 'none'; "
            f"script-src 'nonce-{nonce}'; "
            "img-src * data: blob:; style-src * 'unsafe-inline'; font-src * data:; "
            "media-src *; frame-src 'none'; object-src 'none'; form-action 'none'; "
            "connect-src 'none'")


def inject_picker(html: str, script: str, sid: str = "", revision: int = 1,
                  nonce: str = "") -> str:
    """Дописать в зеркало скрипт подсветки и выбора элементов.

    Код вставляется текстом, а не ссылкой: в зеркале стоит `<base href>` сайта,
    поэтому путь вида `/static/picker.js` уехал бы на чужой origin и не
    загрузился. `nonce` совпадает с CSP ответа — только этот скрипт и
    выполняется.
    """
    attributes = (f' data-sid="{html_escape.escape(sid, quote=True)}"'
                  f' data-revision="{int(revision)}"')
    if nonce:
        attributes += f' nonce="{html_escape.escape(nonce, quote=True)}"'
    tag = f"<script{attributes}>\n{script}\n</script>"
    index = html.lower().rfind("</body>")
    if index == -1:
        return html + tag
    return html[:index] + tag + html[index:]
