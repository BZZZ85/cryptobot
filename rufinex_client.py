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
import time

_cache = {"rates": None, "fetched_at": 0}
CACHE_TTL_SECONDS = 60  # не дёргаем rufinex чаще раза в минуту
RUFINEX_API_URL = "https://api.rufinex.ru/api/rates"  # <- нужен настоящий адрес запроса из DevTools

MARKUP_PERCENT = 5.0  # наценка сверху базового курса


def fetch_base_rates() -> dict | None:
    now = time.time()
    if _cache["rates"] and now - _cache["fetched_at"] < CACHE_TTL_SECONDS:
        return _cache["rates"]

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Referer": "https://rufinex.ru/",
        }
        response = requests.get(RUFINEX_API_URL, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        rates = {"buy": float(data["buy"]), "sell": float(data["sell"])}
        _cache["rates"] = rates
        _cache["fetched_at"] = now
        return rates
    except Exception as e:
        print(f"Ошибка получения курса с rufinex.ru: {e}")
        if _cache["rates"]:
            print("Использую последний известный курс (fallback).")
            return _cache["rates"]
        return None


def compute_price_with_markup(side: str) -> float | None:
    """
    side: 'buy' или 'sell' с точки зрения ТВОЕГО объявления (что делает клиент).
    'buy'  - клиент покупает USDT у тебя -> берём курс rufinex "sell" (дороже)
    'sell' - клиент продаёт USDT тебе -> берём курс rufinex "buy" (дешевле)
    """
    rates = fetch_base_rates()
    if rates is None:
        return None

    rufinex_side = "sell" if side == "buy" else "buy"
    base = rates.get(rufinex_side)
    if base is None:
        return None
    return round(base * (1 + MARKUP_PERCENT / 100), 2)
