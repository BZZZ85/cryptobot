"""
Bitget P2P API. Официальной python-библиотеки нет, поэтому подписываем запросы вручную.
Схема подписи такая же, как у OKX: base64(HMAC_SHA256(secret, timestamp+method+path+body)).

ВАЖНО: точные пути эндпоинтов для получения списка ордеров могут отличаться в зависимости
от версии API у тебя в кабинете - если бот вернёт ошибку 404/401 на функции get_new_orders,
зайди в свой Bitget API Dashboard -> P2P API документация и пришли мне точный путь оттуда,
я поправлю ORDERS_PATH ниже.
"""

import base64
import hashlib
import hmac
import logging
import time

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.bitget.com"
ADS_PATH = "/api/v2/p2p/advList"
ORDERS_PATH = "/api/v2/p2p/orderList"  # <- проверь этот путь в своём кабинете, см. комментарий выше


class BitgetExchange:
    name = "Bitget"
    has_api = True

    def __init__(self, api_key: str, api_secret: str, passphrase: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase

    def _sign(self, timestamp: str, method: str, request_path: str, body: str = ""):
        message = f"{timestamp}{method.upper()}{request_path}{body}"
        mac = hmac.new(self.api_secret.encode(), message.encode(), hashlib.sha256)
        return base64.b64encode(mac.digest()).decode()

    def _get(self, path: str, params: dict = None):
        params = params or {}
        query = "&".join(f"{k}={v}" for k, v in params.items())
        request_path = f"{path}?{query}" if query else path
        timestamp = str(int(time.time() * 1000))
        sign = self._sign(timestamp, "GET", request_path)

        headers = {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": sign,
            "ACCESS-PASSPHRASE": self.passphrase,
            "ACCESS-TIMESTAMP": timestamp,
            "locale": "en-US",
            "Content-Type": "application/json",
        }
        response = requests.get(BASE_URL + request_path, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()

    def get_my_ad(self, side: str, token: str = "USDT", currency: str = "RUB"):
        """Возвращает первое активное своё объявление нужного типа."""
        data = self._get(ADS_PATH, {"limit": 20})
        items = data.get("data", {}).get("advList") or data.get("data", []) or []
        wanted_side = "sell" if side == "sell" else "buy"
        for item in items:
            if item.get("side") == wanted_side and item.get("coin") == token and item.get("fiat") == currency:
                adv_no = item.get("advNo") or item.get("id")
                return {
                    "id": adv_no,
                    "price": float(item.get("price", 0)),
                    "link": f"https://www.bitget.com/p2p-trade/{('sell' if side=='buy' else 'buy')}/{token}?advNo={adv_no}",
                }
        return None

    def get_new_orders(self):
        """Возвращает список новых ордеров по твоим объявлениям."""
        try:
            data = self._get(ORDERS_PATH, {"limit": 20})
        except Exception as e:
            logger.error(f"Bitget: ошибка получения ордеров ({e}). Проверь ORDERS_PATH в bitget_ex.py")
            return []

        items = data.get("data", {}).get("orderList", []) or []
        results = []
        for item in items:
            results.append({
                "order_id": str(item.get("orderId") or item.get("orderNo")),
                "nick": item.get("buyerRealName") or item.get("sellerRealName") or "?",
                "amount": item.get("amount"),
                "currency": item.get("fiat"),
                "quantity": item.get("count"),
                "token": item.get("coin"),
            })
        return results
