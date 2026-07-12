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
    support_url = f"https://t.me/{config.SUPPORT_CONTACT.lstrip('@')}" if config.SUPPORT_CONTACT else None
    social_links = {
        "telegram": config.TELEGRAM_CHANNEL_URL or None,
        "instagram": config.INSTAGRAM_URL or None,
        "support": support_url,
    }
    stats = {"since_year": config.WORKING_SINCE_YEAR, "total_deals": storage.get_total_clicks_count()}
    return {"rates_text": text, "is_admin": is_admin, "rate_age_seconds": age, "social_links": social_links, "stats": stats}

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


@app.get("/api/admin/conversion")
async def api_admin_conversion(days: int = 7, user=Depends(require_admin)):
    return {"conversion": storage.get_conversion_stats(days)}


@app.get("/api/admin/reviews")
async def api_admin_reviews(user=Depends(require_admin)):
    return storage.get_review_stats()
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

    ad["price"] = rufinex_client.apply_loyalty_discount(ad.get("price"), payload.side, user.get("id"))

    username = f"@{user.get('username')}" if user.get("username") else user.get("first_name", "Клиент")
    storage.record_click(payload.exchange, payload.side, username, user.get("id"))

    profile = storage.get_client_profile(user.get("id")) or {}
    profile_line = ""
    if profile.get("full_name"):
        profile_line += f"\n👤 ФИО: {profile['full_name']}"
    if profile.get("exchange_nickname"):
        profile_line += f"\n🏷 Ник на бирже: {profile['exchange_nickname']}"

    side_label = "Купить" if payload.side == "buy" else "Продать"
    amount_line = f" (сумма: {payload.amount})" if payload.amount else ""
    notify_admins(
        f"👉 [Панель] Клиент {username} выбрал: {side_label} USDT/RUB на {exchange.name}"
        + (f" (~{ad['price']:.2f} ₽)" if ad.get("price") else "")
        + amount_line
        + profile_line
    )

    return {"link": ad["link"], "price": ad.get("price"), "exchange_name": exchange.name}


@app.get("/api/client/profile")
async def api_client_profile(user=Depends(get_user)):
    history = storage.get_user_clicks(user["id"], limit=10)
    for h in history:
        ex = EXCHANGES.get(h["exchange"])
        h["exchange_name"] = ex.name if ex else h["exchange"]
        h["created_at"] = h["created_at"].isoformat()
    referral_link = f"https://t.me/{config.BOT_USERNAME}?start=ref_{user['id']}" if config.BOT_USERNAME else None
    profile = storage.get_client_profile(user["id"]) or {}
    deals_count = storage.get_user_deal_count(user["id"])
    return {
        "history": history,
        "referral_link": referral_link,
        "referral_count": storage.get_referral_count(user["id"]),
        "full_name": profile.get("full_name"),
        "exchange_nickname": profile.get("exchange_nickname"),
        "deals_count": deals_count,
        "is_loyal": deals_count >= config.LOYALTY_THRESHOLD_DEALS,
        "loyalty_threshold": config.LOYALTY_THRESHOLD_DEALS,
        "loyalty_discount_percent": config.LOYALTY_DISCOUNT_PERCENT,
    }


class ProfileUpdate(BaseModel):
    full_name: str | None = None
    exchange_nickname: str | None = None


@app.post("/api/client/profile")
async def api_client_update_profile(payload: ProfileUpdate, user=Depends(get_user)):
    storage.update_client_profile(user["id"], full_name=payload.full_name, exchange_nickname=payload.exchange_nickname)
    return {"ok": True}


class PriceAlertRequest(BaseModel):
    side: str
    direction: str
    threshold: float


@app.get("/api/client/alerts")
async def api_client_alerts(user=Depends(get_user)):
    return {"alerts": storage.get_user_alerts(user["id"])}


@app.post("/api/client/alerts")
async def api_client_create_alert(payload: PriceAlertRequest, user=Depends(get_user)):
    if payload.side not in ("buy", "sell") or payload.direction not in ("above", "below"):
        raise HTTPException(status_code=400, detail="Некорректные параметры алерта")
    alert_id = storage.create_price_alert(user["id"], payload.side, payload.direction, payload.threshold)
    return {"ok": True, "id": alert_id}


class AlertCancelRequest(BaseModel):
    id: int


@app.post("/api/client/alerts/cancel")
async def api_client_cancel_alert(payload: AlertCancelRequest, user=Depends(get_user)):
    storage.cancel_alert(payload.id, user["id"])
    return {"ok": True}


app.mount("/", StaticFiles(directory="webapp", html=True), name="static")
