import os
from dotenv import load_dotenv

from exchanges.bybit_ex import BybitExchange
from exchanges.bitget_ex import BitgetExchange
from exchanges.manual_ex import ManualExchange

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
_admin_ids_raw = os.getenv("ADMIN_CHAT_IDS", os.getenv("ADMIN_CHAT_ID", "0"))
ADMIN_CHAT_IDS = [int(x.strip()) for x in _admin_ids_raw.split(",") if x.strip()]
WELCOME_STICKER_ID = os.getenv("WELCOME_STICKER_ID", "")
TOKEN = "USDT"
CURRENCY = "RUB"
SUPPORT_CONTACT = os.getenv("SUPPORT_CONTACT", "@your_username")
TELEGRAM_CHANNEL_URL = os.getenv("TELEGRAM_CHANNEL_URL", "")
INSTAGRAM_URL = os.getenv("INSTAGRAM_URL", "")
CHECK_ORDERS_INTERVAL = 30  # секунд


def _bool(name: str) -> bool:
    return os.getenv(name, "False").lower() == "true"


def build_exchanges() -> dict:
    """Собирает словарь {ключ: объект биржи} только для включённых в .env бирж."""
    exchanges = {}

    if _bool("BYBIT_ENABLED"):
        exchanges["bybit"] = BybitExchange(
            api_key=os.getenv("BYBIT_API_KEY"),
            api_secret=os.getenv("BYBIT_API_SECRET"),
            testnet=_bool("BYBIT_TESTNET"),
        )

    if _bool("BITGET_ENABLED"):
        exchanges["bitget"] = BitgetExchange(
            api_key=os.getenv("BITGET_API_KEY"),
            api_secret=os.getenv("BITGET_API_SECRET"),
            passphrase=os.getenv("BITGET_API_PASSPHRASE"),
        )

    if _bool("HTX_ENABLED"):
        exchanges["htx"] = ManualExchange(name="HTX", key="htx")

    if _bool("MEXC_ENABLED"):
        exchanges["mexc"] = ManualExchange(name="MEXC", key="mexc")

    return exchanges
