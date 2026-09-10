"""Интерфейс регистратора. Новый сервис = новый подкласс с этими методами.

Раньше здесь жил интерфейс прокси-пула zcode-farm (квоты, SSE, нативный чат).
Регистратору это не нужно: остались регистрация аккаунта и заголовки для
проверки ключа.
"""


class BaseProvider:
    name = "base"

    async def register_step(self, log, semi_auto=False, proxy=None):
        """Один шаг регистрации. log(msg) — синхронный колбэк для событий в UI.

        semi_auto=True: навигация и заполнение — best-effort; проверку сайта
        (капча/политика) и отправку формы выполняет человек, регистратор
        продолжает после письма.
        Должен вернуть dict с ключами email, token, provider, password,
        device_uuid, confirmed.
        """
        raise NotImplementedError

    def get_headers(self, account):
        """Заголовки авторизации аккаунта — например, чтобы проверить ключ."""
        raise NotImplementedError
