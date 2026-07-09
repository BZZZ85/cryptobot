"""
Получение базового курса USDT/RUB с rufinex.ru.
ВАЖНО: сейчас это заглушка - точный способ получения курса нужно уточнить
(см. инструкцию в чате про DevTools -> Network). Как только узнаем URL API,
здесь появится реальный запрос.
"""

import requests

MARKUP_PERCENT = 5.0  # наценка сверху базового курса


def fetch_base_rate() -> float | None:
    """Возвращает базовый курс USDT/RUB с rufinex.ru или None при ошибке."""
    try:
        # TODO: заменить на реальный запрос, когда узнаем API endpoint сайта
        raise NotImplementedError("Endpoint rufinex.ru ещё не подключен")
    except Exception as e:
        print(f"Ошибка получения курса с rufinex.ru: {e}")
        return None


def compute_price_with_markup() -> float | None:
    base = fetch_base_rate()
    if base is None:
        return None
    return round(base * (1 + MARKUP_PERCENT / 100), 2)
