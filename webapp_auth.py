"""
Проверка подлинности данных, которые Telegram передаёт Mini App (initData).
Без этого кто угодно смог бы прислать поддельный "я админ" и получить доступ.
"""

import hashlib
import time
import hmac
from urllib.parse import parse_qsl

import config


def validate_init_data(init_data: str) -> dict | None:
    """Проверяет подпись initData от Telegram WebApp. Возвращает данные пользователя или None."""
    try:
        parsed = dict(parse_qsl(init_data))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None

        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", config.TELEGRAM_BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if calculated_hash != received_hash:
            return None

        auth_date = int(parsed.get("auth_date", 0))
        if time.time() - auth_date > 86400:  # старше суток - подозрительно, отклоняем
            return None

        import json
        user = json.loads(parsed.get("user", "{}"))
        return user
    except Exception:
        return None
