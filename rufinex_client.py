"""
Получение базового курса USDT/RUB с rufinex.ru.
Эндпоинт подтверждён вручную через DevTools -> Network (10.07.2026):
GET https://api.rufinex.ru/api/rates -> {"buy": "79.3", "sell": "79.8"}
"""

import requests
import time

import config
import storage

_cache = {"rates": None, "fetched_at": 0}
CACHE_TTL_SECONDS = 60  # не дёргаем rufinex чаще раза в минуту
RUFINEX_API_URL = "https://api.rufinex.ru/api/rates"

DEFAULT_MARKUP_PERCENT = 5.0  # наценка по умолчанию, пока админ не задал свою через /setmarkup
_markup_cache = {"value": None, "fetched_at": 0}
MARKUP_CACHE_TTL_SECONDS = 60


def get_markup_percent() -> float:
    """Текущая наценка в процентах. Настраивается командой /setmarkup, хранится в БД, кэшируется на минуту."""
    now = time.time()
    if _markup_cache["value"] is not None and now - _markup_cache["fetched_at"] < MARKUP_CACHE_TTL_SECONDS:
        return _markup_cache["value"]
    try:
        value = float(storage.get_setting("markup_percent", str(DEFAULT_MARKUP_PERCENT)))
    except Exception:
        value = DEFAULT_MARKUP_PERCENT
    _markup_cache["value"] = value
    _markup_cache["fetched_at"] = now
    return value


def set_markup_percent(value: float):
    storage.set_setting("markup_percent", str(value))
    _markup_cache["value"] = value
    _markup_cache["fetched_at"] = time.time()


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
    return round(base * (1 + get_markup_percent() / 100), 2)
def get_cache_age_seconds() -> int | None:
    """Сколько секунд назад в последний раз реально обновлялся курс. None, если ещё не запрашивали."""
    if not _cache["rates"]:
        return None
    return int(time.time() - _cache["fetched_at"])


def apply_loyalty_discount(price: float | None, side: str, chat_id: int | None) -> float | None:
    """
    Улучшает цену постоянным клиентам (>= LOYALTY_THRESHOLD_DEALS сделок).
    side='buy'  (клиент покупает у нас) -> цена ниже, выгоднее клиенту.
    side='sell' (клиент продаёт нам)    -> цена выше, выгоднее клиенту.
    """
    if price is None or chat_id is None:
        return price
    if storage.get_user_deal_count(chat_id) < config.LOYALTY_THRESHOLD_DEALS:
        return price
    discount = config.LOYALTY_DISCOUNT_PERCENT / 100
    factor = (1 - discount) if side == "buy" else (1 + discount)
    return round(price * factor, 2)


def is_loyal_client(chat_id: int | None) -> bool:
    if chat_id is None:
        return False
    return storage.get_user_deal_count(chat_id) >= config.LOYALTY_THRESHOLD_DEALS


def get_available_limit(side: str) -> float | None:
    """Доступный объём в рублях, который админ готов обработать по этой стороне сделки.
    Задаётся командой /setlimit, хранится в settings. None - лимит не задан (без ограничений)."""
    value = storage.get_setting(f"limit_{side}")
    try:
        return float(value) if value else None
    except ValueError:
        return None


def set_available_limit(side: str, amount: float | None):
    storage.set_setting(f"limit_{side}", str(amount) if amount else "")


def format_limit(amount: float) -> str:
    return f"{amount:,.0f} ₽".replace(",", " ")


def check_amount_limit(amount, side: str) -> str | None:
    """Если сумма, введённая клиентом, больше заданного лимита - возвращает текст предупреждения."""
    limit = get_available_limit(side)
    if not limit or not amount:
        return None
    cleaned = str(amount).lower().replace(" ", "").replace(",", ".").replace("₽", "").replace("руб", "").replace("usdt", "")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if value > limit:
        return f"⚠️ Сумма больше доступного объёма ({format_limit(limit)}) — уточни у поддержки перед сделкой."
    return None
