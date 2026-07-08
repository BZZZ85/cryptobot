"""
Bybit P2P - полная автоматика через официальную библиотеку bybit_p2p.
Возможности: посмотреть свои объявления, их прямую ссылку, и новые входящие ордера.
"""

import logging
from bybit_p2p import P2P

logger = logging.getLogger(__name__)


class BybitExchange:
    name = "Bybit"
    has_api = True

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        self.api = P2P(testnet=testnet, api_key=api_key, api_secret=api_secret)

    def get_my_ad(self, side: str, token: str = "USDT", currency: str = "RUB"):
        """
        side: "buy" или "sell" (с точки зрения твоего объявления)
        Возвращает первое активное объявление данного типа: {"id", "price", "link"}
        """
        bybit_side = "1" if side == "buy" else "0"  # 1 = ты покупаешь, 0 = ты продаёшь (см. доки Bybit)
        response = self.api.get_ads_list()
        items = response.get("result", {}).get("items", [])
        for item in items:
            if str(item.get("side")) == bybit_side and item.get("tokenId") == token and item.get("currencyId") == currency:
                item_id = item["id"]
                return {
                    "id": item_id,
                    "price": float(item.get("price", 0)),
                    # Прямая ссылка на объявление в приложении/сайте Bybit
                    "link": f"https://www.bybit.com/fiat/trade/otc/?actionType={'1' if side == 'buy' else '0'}&token={token}&fiat={currency}&paymentMethod=&itemId={item_id}",
                }
        return None

    def get_new_orders(self):
        """Возвращает список новых (ещё не виденных) ордеров: {"order_id", "nick", "amount", "currency"}"""
        response = self.api.get_pending_orders(page=1, size=20)
        items = response.get("result", {}).get("items", [])
        results = []
        for item in items:
            results.append({
                "order_id": str(item["id"]),
                "nick": item.get("targetNickName", "?"),
                "amount": item.get("amount"),
                "currency": item.get("currencyId"),
                "quantity": item.get("notifyTokenQuantity") or item.get("quantity"),
                "token": item.get("tokenId"),
            })
        return results
