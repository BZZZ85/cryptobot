"""
"Ручная" биржа: для HTX, MEXC и любой другой площадки без открытого P2P API.

Ты сам создаёшь объявление на сайте биржи, а ссылку и актуальную цену вписываешь
боту командами /setlink и /setprice. Бот просто отдаёт эту ссылку клиенту, но не
может сам узнать, когда клиент создал ордер - это нужно проверять в приложении биржи.
"""

import storage


class ManualExchange:
    has_api = False

    def __init__(self, name: str, key: str):
        self.name = name
        self.key = key  # внутренний ключ для storage.py, например "htx" или "mexc"

    def get_my_ad(self, side: str, token: str = "USDT", currency: str = "RUB"):
        ad = storage.get_manual_ad(self.key, side)
        if not ad or not ad.get("url"):
            return None
        return {
            "id": None,
            "price": ad.get("price"),
            "link": ad["url"],
        }

    def get_new_orders(self):
        # Ручная биржа не умеет сама сообщать о новых ордерах
        return []
