"""
Получение базового курса USDT/RUB с rufinex.ru.
ВАЖНО: сейчас это заглушка - точный способ получения курса нужно уточнить
(см. инструкцию в чате про DevTools -> Network). Как только узнаем URL API,
здесь появится реальный запрос.
"""

"""
Получение базового курса USDT/RUB с rufinex.ru.
"""

import requests

RUFINEX_API_URL = "https://api.rufinex.ru/api/rates"  # <- нужен настоящий адрес запроса из DevTools

MARKUP_PERCENT = 5.0  # наценка сверху базового курса


def fetch_base_rates() -> dict | None:
    """Возвращает {"buy": float, "sell": float} с rufinex.ru или None при ошибке."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Referer": "https://rufinex.ru/",
        }
        response = requests.get(RUFINEX_API_URL, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        return {
            "buy": float(data["buy"]),
            "sell": float(data["sell"]),
        }
    except Exception as e:
        print(f"Ошибка получения курса с rufinex.ru: {e}")
        return None


def compute_price_with_markup(side: str) -> float | None:
    """side: 'buy' или 'sell' - какую сторону курса брать за базу."""
    rates = fetch_base_rates()
    if rates is None:
        return None
    base = rates.get(side)
    if base is None:
        return None
    return round(base * (1 + MARKUP_PERCENT / 100), 2)
