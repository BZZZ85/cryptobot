"""
Веб-панель (Telegram Mini App). Фаза 1: авторизация + просмотр курсов.
Запускается ОТДЕЛЬНЫМ сервисом на Railway (не там же, где bot.py).
"""

from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

import config
import webapp_auth
from bot import build_rates_text, EXCHANGES  # переиспользуем ту же логику и список бирж, что и в боте  # переиспользуем ту же логику, что и в боте
from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel
import requests
import storage
import rufinex_client

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_user(authorization: str = Header(default="")):
    user = webapp_auth.validate_init_data(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="Не удалось подтвердить, что это Telegram")
    return user


@app.get("/api/rates")
async def api_rates(authorization: str = Header(default="")):
    user = get_user(authorization)
    is_admin = user.get("id") in config.ADMIN_CHAT_IDS
    text = await build_rates_text()
    age = rufinex_client.get_cache_age_seconds()
    return {"rates_text": text, "is_admin": is_admin, "rate_age_seconds": age}

class AdUpdate(BaseModel):
    exchange: str
    side: str
    url: str | None = None
    price: float | None = None


class AdSideKey(BaseModel):
    exchange: str
    side: str


def require_admin(authorization: str = Header(default="")):
    user = get_user(authorization)
    if user.get("id") not in config.ADMIN_CHAT_IDS:
        raise HTTPException(status_code=403, detail="Только для админов")
    return user


@app.get("/api/admin/ads")
async def api_admin_ads(user=Depends(require_admin)):
    result = []
    for key, exchange in EXCHANGES.items():
        if exchange.has_api:
            continue  # показываем только "ручные" биржи - у Bybit/Bitget объявления управляются иначе
        for side in ("buy", "sell"):
            ad = storage.get_manual_ad(key, side)
            result.append({
                "exchange": key,
                "exchange_name": exchange.name,
                "side": side,
                "url": ad.get("url"),
                "price": ad.get("price"),
            })
    return {"ads": result}


@app.post("/api/admin/ads")
async def api_admin_update_ad(payload: AdUpdate, user=Depends(require_admin)):
    storage.set_manual_ad(payload.exchange, payload.side, url=payload.url, price=payload.price)
    return {"ok": True}


@app.post("/api/admin/ads/reset_price")
async def api_admin_reset_price(payload: AdSideKey, user=Depends(require_admin)):
    storage.delete_manual_price(payload.exchange, payload.side)
    return {"ok": True}


@app.post("/api/admin/ads/delete")
async def api_admin_delete_ad(payload: AdSideKey, user=Depends(require_admin)):
    storage.delete_manual_ad(payload.exchange, payload.side)
    return {"ok": True}
@app.get("/api/admin/rate_history")
async def api_rate_history(hours: int = 24, user=Depends(require_admin)):
    return {"history": storage.get_rate_history(hours)}


@app.get("/api/admin/click_history")
async def api_click_history(days: int = 7, user=Depends(require_admin)):
    return {"history": storage.get_click_history(days)}
def notify_admins(text: str):
    """Шлём уведомление напрямую через Telegram HTTP API (без объекта бота - мы отдельный процесс)."""
    for admin_id in config.ADMIN_CHAT_IDS:
        try:
            requests.post(
                f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": admin_id, "text": text},
                timeout=10,
            )
        except Exception:
            pass


@app.get("/api/client/exchanges")
async def api_client_exchanges(side: str, user=Depends(get_user)):
    if side not in ("buy", "sell"):
        raise HTTPException(status_code=400, detail="side должен быть buy или sell")

    result = []
    for key, exchange in EXCHANGES.items():
        try:
            ad = exchange.get_my_ad(side=side, token=config.TOKEN, currency=config.CURRENCY)
        except Exception:
            ad = None
        if not ad or not ad.get("link"):
            continue
        result.append({
            "key": key,
            "name": exchange.name,
            "price": ad.get("price"),
            "logo": f"/logos/{key}.png",
        })
    return {"exchanges": result}


class ClientDeliverRequest(BaseModel):
    exchange: str
    side: str
    amount: str | None = None


@app.post("/api/client/deliver")
async def api_client_deliver(payload: ClientDeliverRequest, user=Depends(get_user)):
    exchange = EXCHANGES.get(payload.exchange)
    if exchange is None:
        raise HTTPException(status_code=404, detail="Биржа не найдена")

    ad = exchange.get_my_ad(side=payload.side, token=config.TOKEN, currency=config.CURRENCY)
    if not ad or not ad.get("link"):
        raise HTTPException(status_code=404, detail="Объявление недоступно")

    username = f"@{user.get('username')}" if user.get("username") else user.get("first_name", "Клиент")
    storage.record_click(payload.exchange, payload.side, username)

    side_label = "Купить" if payload.side == "buy" else "Продать"
    amount_line = f" (сумма: {payload.amount})" if payload.amount else ""
    notify_admins(
        f"👉 [Панель] Клиент {username} выбрал: {side_label} USDT/RUB на {exchange.name}"
        + (f" (~{ad['price']:.2f} ₽)" if ad.get("price") else "")
        + amount_line
    )

    return {"link": ad["link"], "price": ad.get("price"), "exchange_name": exchange.name}
app.mount("/", StaticFiles(directory="webapp", html=True), name="static")
