"""
"Ручная" биржа: для HTX, MEXC и любой другой площадки без открытого P2P API.

Ты сам создаёшь объявление на сайте биржи, а ссылку и актуальную цену вписываешь
боту командами /setlink и /setprice. Бот просто отдаёт эту ссылку клиенту, но не
может сам узнать, когда клиент создал ордер - это нужно проверять в приложении биржи.
"""

import storage
import rufinex_client


class ManualExchange:
    has_api = False

    def __init__(self, name: str, key: str):
        self.name = name
        self.key = key

    def get_my_ad(self, side: str, token: str = "USDT", currency: str = "RUB"):
        ad = storage.get_manual_ad(self.key, side)
        if not ad or not ad.get("url"):
            return None

        # Если цена задана вручную - используем её. Иначе считаем rufinex + 5%.
        if ad.get("price") is not None:
            price = ad["price"]
            price_source = "manual"
        else:
            price = rufinex_client.compute_price_with_markup(side)
            price_source = "auto"

        return {
            "id": None,
            "price": price,
            "price_source": price_source,
            "link": ad["url"],
        }

    def get_new_orders(self):
        return []
