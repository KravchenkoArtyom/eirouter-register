"""Интерфейс провайдера. Новый сервис = новый подкласс с этими методами."""

from typing import Optional


class BaseProvider:
    name = "base"

    # Ключ, под которым в пуле лежит остаток, если сервис считает лимит НЕ по
    # моделям, а общим счётчиком (кредиты). None - лимит пер-модельный, и
    # остаток хранится под именем модели.
    CREDIT_QUOTA_KEY = None

    # Провайдер сам строит апстрим-запрос и переводит ответ (не "подмена
    # заголовков + прозрачный проброс"). Прокси включает нативную ветку только
    # при NATIVE_CHAT=True; иначе всё работает по-старому (см. zcode). Пример -
    # qoder: свой эндпоинт, кастомная кодировка тела, COSY-подпись, свой SSE.
    NATIVE_CHAT = False

    # Маркеры "живого" контента для sniff-фазы (ProxyServer._sniff_stream). None -
    # берутся дефолтные COMMIT_MARKERS прокси. Нативный SSE (qoder заворачивает
    # чанк в {"body": "..."}) не совпадает с дефолтными, поэтому провайдер задаёт свои.
    commit_markers = None

    async def register_step(self, log, semi_auto=False, proxy=None):
        """Один шаг регистрации. log(msg) - синхронный колбэк для событий в GUI.

        semi_auto=True: навигация и заполнение - best-effort; проверку сайта
        (капча/политика) и отправку формы выполняет человек, бот продолжает
        после письма.
        Должен вернуть dict с ключами email, token, provider, password,
        device_uuid, confirmed.
        """
        raise NotImplementedError

    def quota_key(self, model):
        """Под каким ключом искать/писать остаток для запроса к этой модели.

        Пер-модельные лимиты (zcode) - это сама модель; кредитный баланс
        (qoder) - один общий ключ на аккаунт.
        """
        return self.CREDIT_QUOTA_KEY or model

    async def fetch_quota(self, account):
        """Остатки аккаунта: {ключ_квоты: остаток} или None, если не удалось."""
        raise NotImplementedError

    def get_headers(self, account):
        """Заголовки для подмены auth и телеметрии на стороне прокси."""
        raise NotImplementedError

    def detect_quota_limit(self, status, body):
        """Классификация ответа: 'rate' (квота/лимит) | 'auth' (401/403) | None."""
        raise NotImplementedError

    def instream_error(self, body) -> Optional[str]:
        """Ошибка лимита/авторизации ВНУТРИ 200-ответа (SSE/JSON).

        Апстримы вроде Qoder отдают исчерпание кодом 115 в теле при
        HTTP 200: статус-код ротацию не триггерит, и без этого хука
        клиенту ушёл бы поток с ошибкой вместо контента.
        Возврат: 'rate' | 'auth' | None (живой ответ).
        """
        return None

    # ---- нативный чат (только при NATIVE_CHAT=True) ----
    async def build_request(self, path, body, account, model, client):
        """Собрать апстрим-запрос провайдера или None для прозрачного проброса.

        Возврат: (method, url, headers, wire_body: bytes) - прокси отправит его
        как есть (см. ProxyServer._send_with_rotation), сохраняя ротацию/квоты.
        None - обычный путь (base_url аккаунта + get_headers). client - httpx с
        прокси аккаунта: можно доехать до вспомогательных эндпоинтов (обмен PAT).
        """
        return None

    async def refresh_session(self, account, client):
        """Пересобрать протухшую сессию аккаунта (код 105) и вернуть успех.

        Прокси вызовет перед баном аккаунта и повторит запрос на ТОМ ЖЕ аккаунте.
        """
        return False

    async def to_openai_sse(self, upstream_bytes, model):
        """Нативный апстрим-поток -> OpenAI SSE (chat.completion.chunk ... [DONE])."""
        raise NotImplementedError

    async def to_anthropic_sse(self, upstream_bytes, model):
        """Нативный апстрим-поток -> Anthropic SSE (message_start ... message_stop)."""
        raise NotImplementedError

    async def collect_anthropic(self, upstream_bytes, model):
        """Нативный апстрим-поток -> единый Anthropic-message dict (нестриминг)."""
        raise NotImplementedError

    async def list_models(self, account, client):
        """Живой каталог моделей провайдера [{id, root?, display_name?, ...}] или None.

        None - прокси берёт стартовый набор из MODELS + квот. account может быть
        None (нет активных): провайдер решает сам (напр. вернуть статические алиасы).
        """
        return None
