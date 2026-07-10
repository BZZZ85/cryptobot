"""
Веб-панель (Telegram Mini App). Фаза 1: авторизация + просмотр курсов.
Запускается ОТДЕЛЬНЫМ сервисом на Railway (не там же, где bot.py).
"""

from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

import config
import webapp_auth
from bot import build_rates_text  # переиспользуем ту же логику, что и в боте

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
    return {"rates_text": text, "is_admin": is_admin}


app.mount("/", StaticFiles(directory="webapp", html=True), name="static")
