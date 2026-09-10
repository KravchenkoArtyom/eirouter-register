"""Веб-клиент tmail (tmail.wibucrypto.pro): API нет, всё через Playwright.

Создание ящика автоматизировано: клик "Новый" -> имя в #user -> "Создайте" ->
чтение адреса (инпуты / клипборд по кнопке "Копировать" / текст страницы).
Ссылки подтверждения ловятся так: каждый цикл жмём Refresh (Livewire сам
список не обновляет) -> открываем первое письмо -> сниффер ответов +
скан DOM/iframe на ссылку.
"""

import asyncio
import html
import re
import time


class TmailWebClient:
    MAILBOX_URL = "https://tmail.wibucrypto.pro/mailbox"
    TMAIL_HOST = "wibucrypto.pro"
    EMAIL_REGEX = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    ADDRESS_BLACKLIST = ("support", "admin", "noreply", "no-reply", "example", "abuse", "postmaster")
    # legal/policy-ссылки сайта не являются ссылкой подтверждения
    EXCLUDE_PATH_HINTS = ("legal", "privacy", "terms", "policy", "agreement", "cookie")

    # ---- селекторы интерфейса создания ящика (Alpine.js + Livewire) ----
    NEW_BTN_SELECTOR = 'div[x-on\\:click="in_app = true"]'
    # текст кнопки зависит от языка интерфейса (ru/en/uk): ищем по иконке/структуре
    NEW_BTN_TEXTS = ("Новый", "New", "Нове", "Create", "Создать")
    USER_INPUT_SELECTOR = "#user"
    CREATE_BTN_SELECTOR = "#create"
    COPY_BTN_SELECTOR = "div.btn_copy"
    # Refresh: Livewire-кнопка (wire:click="$emit('fetchMessages')"); тем же
    # атрибутом обладает только Delete - фильтруем по тексту
    REFRESH_BTN_SELECTOR = 'div[wire\\:click]'
    REFRESH_BTN_TEXT = re.compile(r"refresh|обновить", re.I)

    # ---- селекторы-кандидаты для клика по первому письму в списке ----
    MESSAGE_ITEM_SELECTORS = (
        ".mail-item", ".email-item", ".message-item", ".mail-list li",
        ".mail-list a", "li[class*='mail']", "tr[class*='mail']",
        "div[class*='message']", "div[class*='letter']",
    )

    def __init__(self, link_regex, link_keywords, log):
        self.link_pattern = re.compile(link_regex)
        self.link_keywords = tuple(k.lower() for k in link_keywords)
        self.log = log
        self.page = None
        self.address = None
        self._pending = []
        self._links = []
        self._collect_since = 0.0

    # ---- открытие вкладки ----
    async def open(self, context):
        self.page = await context.new_page()
        try:
            await context.grant_permissions(["clipboard-read", "clipboard-write"])
        except Exception:
            pass
        self.page.on("response", self._on_response_sync)
        await self.page.goto(self.MAILBOX_URL, wait_until="domcontentloaded",
                             timeout=120_000)

    # ---- создание ящика (автоматическое) ----
    async def create_mailbox(self, username, timeout=120):
        await self._click_new_button()
        await self._type_username(username)
        await self._click_create_button()
        self.log("[tmail] ящик создаётся, читаю адрес...")
        self.address = await self._wait_address(timeout)
        self.log(f"[tmail] адрес прочитан: {self.address}")
        return self.address

    async def _click_new_button(self):
        # сначала по структуре: div с нужным x-on:click (самый надёжный признак)
        target = self.page.locator(self.NEW_BTN_SELECTOR).first
        if await target.count():
            try:
                await target.click(timeout=3000)
                return
            except Exception:
                pass
        # fallback: текст на разных языках
        for txt in self.NEW_BTN_TEXTS:
            try:
                await self.page.get_by_text(txt, exact=True).first.click(timeout=2000)
                return
            except Exception:
                continue
        # последний шанс: любой div с x-on:click, у которого в тексте "New"/"Нов"
        await self.page.locator('div[x-on\\:click]').filter(
            has_text=re.compile(r"нов|new", re.I)).first.click()

    async def _type_username(self, username):
        inp = self.page.locator(self.USER_INPUT_SELECTOR).first
        await inp.wait_for(state="visible", timeout=15000)
        await inp.fill(username)

    async def _click_create_button(self):
        btn = self.page.locator(self.CREATE_BTN_SELECTOR).first
        await btn.wait_for(state="visible", timeout=15000)
        await btn.click()

    async def _wait_address(self, timeout=120, interval=2):
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            addr = await self._read_address()
            if addr:
                return addr
            await asyncio.sleep(interval)
        raise TimeoutError("не удалось прочитать адрес ящика после создания")

    async def _read_address(self):
        # 1) значения инпутов
        try:
            # eval_on_selector_all, не eval_on_all: метода eval_on_all в
            # Playwright нет, и вызов молча уходил в except - адрес читался
            # только через клипборд/текст страницы
            values = await self.page.eval_on_selector_all(
                "input", "els => els.map(e => e.value || '')")
            for v in values:
                v = v.strip()
                if re.fullmatch(self.EMAIL_REGEX, v):
                    return v
        except Exception:
            pass
        # 2) клипборд через кнопку "Копировать"
        try:
            copy_btn = self.page.locator(self.COPY_BTN_SELECTOR).first
            if await copy_btn.count() and await copy_btn.is_visible():
                await copy_btn.click(timeout=1500)
                await asyncio.sleep(0.4)
                text = await self.page.evaluate("() => navigator.clipboard.readText().catch(() => '')")
                text = (text or "").strip()
                if re.fullmatch(self.EMAIL_REGEX, text):
                    return text
        except Exception:
            pass
        # 3) текст всей страницы
        try:
            body = await self.page.inner_text("body")
            for c in re.findall(self.EMAIL_REGEX, body):
                if not any(b in c.lower() for b in self.ADDRESS_BLACKLIST):
                    return c
        except Exception:
            pass
        return None

    # ---- сетевой сниффер ----
    def _on_response_sync(self, response):
        self._pending.append(response)

    def start_collecting(self):
        # ответы, накопившиеся до старта сбора, относятся к созданию ящика -
        # сбрасываем, иначе при дрейне получат свежий timestamp и замусорят выборку
        self._pending.clear()
        self._links = []
        self._collect_since = time.time()

    async def _drain_responses(self):
        while self._pending:
            response = self._pending.pop(0)
            try:
                ct = response.headers.get("content-type", "")
                if not any(t in ct for t in ("json", "html", "text", "plain", "javascript")):
                    continue
                body = await response.text()
            except Exception:
                continue
            # JSON часто экранирует / как \/ и & как \u0026 - регэксп на них
            # либо не матчится, либо обрезает ссылку на backslash
            for src in (body, body.replace("\\/", "/").replace("\\u0026", "&")):
                for m in self.link_pattern.finditer(src):
                    self._links.append((m.group(0), time.time()))

    # ---- ссылка подтверждения из письма ----
    async def _click_first_message(self):
        for sel in self.MESSAGE_ITEM_SELECTORS:
            try:
                el = self.page.locator(sel).first
                if await el.count() and await el.is_visible():
                    await el.click(timeout=2000)
                    return True
            except Exception:
                continue
        return False

    async def _click_refresh(self):
        """Кнопка Refresh: Livewire сам новые письма в список отдаёт не всегда."""
        try:
            btn = self.page.locator(self.REFRESH_BTN_SELECTOR).filter(
                has_text=self.REFRESH_BTN_TEXT).first
            if await btn.count() and await btn.is_visible():
                await btn.click(timeout=2000)
                return True
        except Exception:
            pass
        return False

    async def refresh(self):
        """Публичное обновление ящика: используется шагом mail_refresh."""
        return await self._click_refresh()

    async def _scan_dom_for_link(self):
        # только вкладка tmail: на странице регистрации есть свои legal-ссылки
        # (Privacy Policy и т.п.) - они не являются ссылкой подтверждения
        for frame in self.page.frames:
            try:
                html = await frame.content()
            except Exception:
                continue
            for m in self.link_pattern.finditer(html):
                yield m.group(0)

    @staticmethod
    def _clean_url(u):
        """Из HTML ссылка приходит с &amp; вместо & - иначе сервер получает
        мусорный параметр amp;... и страница верификации ломается."""
        u = html.unescape(u or "")
        return u.rstrip(").,;'\"]").rstrip("&")

    def _pick_link(self, candidates):
        # без ключевого слова ссылка подтверждением не считается:
        # иначе подхватывалась Privacy Policy со страницы самого сайта
        for u in candidates:
            u = self._clean_url(u)
            if not u:
                continue
            low = u.lower()
            if any(e in low for e in self.EXCLUDE_PATH_HINTS):
                continue
            if any(k in low for k in self.link_keywords):
                return u
        return None

    async def wait_for_link(self, timeout=300, interval=3):
        start = time.monotonic()
        had_message = False
        while time.monotonic() - start < timeout:
            await self._drain_responses()
            fresh_net = [u for u, ts in self._links if ts >= self._collect_since]
            link = self._pick_link(fresh_net)
            if link:
                return link

            # 1) Refresh: Livewire сам новые письма в список отдаёт не всегда
            await self._click_refresh()
            await asyncio.sleep(interval)
            await self._drain_responses()
            fresh_net = [u for u, ts in self._links if ts >= self._collect_since]
            link = self._pick_link(fresh_net)
            if link:
                return link

            # 2) открываем письмо (каждый цикл - оно могло только что прийти)
            clicked = await self._click_first_message()
            if clicked and not had_message:
                self.log("[tmail] письмо появилось, читаю тело...")
            had_message = clicked
            if clicked:
                await asyncio.sleep(1.5)  # телу письма нужно время загрузиться

            # 3) сканируем DOM/iframe открытого письма
            dom_links = [u async for u in self._scan_dom_for_link()]
            link = self._pick_link(dom_links)
            if link:
                return link

            await asyncio.sleep(1)
        raise TimeoutError("ссылка подтверждения не пришла за отведённое время")

    # ---- код подтверждения из письма (OTP) ----
    # слова, после которых в письме идёт код (en/ru/zh)
    CODE_KEYWORDS = ("code", "otp", "pin", "verification", "verify", "passcode",
                     "код", "验证码", "验证")
    # число - часть CSS-значения (padding:12 / 0x1f / #123456), а не код
    _CODE_NOISE_BEFORE = re.compile(
        r"(?:#|0x)\s*$"
        r"|(?:width|height|padding|margin|font|border|size|top|left|right|bottom"
        r"|rgb|rgba|line|spacing|radius|weight)[^A-Za-z0-9]{0,4}$",
        re.I)
    _CODE_NOISE_AFTER = re.compile(r"^\s*(?:px|pt|em|rem|%|deg)", re.I)

    @staticmethod
    def _strip_markup(text):
        """Снимает style/script, теги и HTML-эскейпы.

        Числа из разметки (width:600px, #123456, Message-ID: <483920@mail>)
        живут в атрибутах и заголовках - после снятия тегов они не конкурируют
        с кодом из текста письма.
        """
        text = re.sub(r"(?is)<(script|style)\b.*?</\1\s*>", " ", text or "")
        text = re.sub(r"(?s)<[^>]{1,400}>", " ", text)
        return html.unescape(text)

    @classmethod
    def _pick_code(cls, sources, length):
        r"""Код длины length из письма.

        Границы - (?<!\d)...(?!\d): \b срабатывает и внутри '12345678',
        отдавая случайный срез длинного числа. Приоритет у числа, перед
        которым стоит слово вида 'code' - иначе берём первое изолированное
        число, не похожее на CSS-значение.
        """
        pattern = re.compile(r"(?<!\d)(\d{%d})(?!\d)" % length)
        fallback = None
        for text, _ts in sources:
            clean = cls._strip_markup(text)
            low = clean.lower()
            for m in pattern.finditer(clean):
                before = low[max(0, m.start() - 80):m.start()]
                after = low[m.end():m.end() + 8]
                if cls._CODE_NOISE_BEFORE.search(before) or cls._CODE_NOISE_AFTER.match(after):
                    continue
                if any(k in before for k in cls.CODE_KEYWORDS):
                    return m.group(1)
                if fallback is None:
                    fallback = m.group(1)
        return fallback

    async def wait_for_code(self, length=6, timeout=600, interval=3, sender_hint=None):
        """Ждёт письмо и достаёт код ТОЛЬКО из текста открытого письма.

        Цикл: Refresh -> письмо появилось -> открыли -> текст письма (DOM всех
        фреймов) -> сначала число рядом со словами code/код/验证码, потом любое
        изолированное длины length.

        Сеть и служебные ответы почтового движка намеренно НЕ сканируются:
        в Livewire/base64-пейлоадах полно случайных цифровых
        последовательностей, и «кодом» становилась абракадабра ещё до прихода
        письма. sender_hint: если задан (например 'qoder'), код берём только
        из письма, в тексте которого есть это слово, - чужие письма пропускаем.
        """
        start = time.monotonic()
        had_message = False
        while time.monotonic() - start < timeout:
            await self._click_refresh()
            await asyncio.sleep(interval)
            clicked = await self._click_first_message()
            if clicked and not had_message:
                self.log("[tmail] письмо появилось, открываю...")
            had_message = had_message or clicked
            if not clicked:
                continue
            await asyncio.sleep(1.5)  # телу письма нужно время загрузиться

            text = await self._page_text()
            if sender_hint and sender_hint.lower() not in text.lower():
                self.log(f"[tmail] письмо не от {sender_hint} - жду нужное")
                continue
            code = self._pick_code([(text, time.time())], length)
            if code:
                return code
        raise TimeoutError(f"код подтверждения ({length} цифр) не пришёл за отведённое время")

    async def _page_text(self):
        """Видимый текст страницы по всем фреймам.

        inner_text не включает атрибуты и скрипты - Livewire/base64-мусор
        в кандидаты кода не попадает, в отличие от frame.content().
        """
        parts = []
        for frame in self.page.frames:
            try:
                parts.append(await frame.locator("body").inner_text(timeout=2000))
            except Exception:
                continue
        return "\n".join(parts)
