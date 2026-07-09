"""
Простое файловое хранилище (JSON), чтобы не поднимать отдельную базу данных.

Хранит:
    - manual_ads: ссылки и цены для бирж без API (HTX, MEXC) -
      manual_ads["htx"]["buy"] = {"url": "...", "price": 95.5}
    - seen_order_ids: список ID ордеров, о которых уже уведомили админа
      (чтобы не слать одно и то же уведомление повторно)
"""

import json
import os
import threading

DATA_FILE = os.path.join(os.path.dirname(__file__), "data.json")
_lock = threading.Lock()

_DEFAULT = {
    "manual_ads": {},
    "seen_order_ids": [],
}


def _load() -> dict:
    if not os.path.exists(DATA_FILE):
        return dict(_DEFAULT)
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def set_manual_ad(exchange: str, side: str, url: str = None, price: float = None):
    with _lock:
        data = _load()
        data.setdefault("manual_ads", {}).setdefault(exchange, {}).setdefault(side, {})
        if url is not None:
            data["manual_ads"][exchange][side]["url"] = url
        if price is not None:
            data["manual_ads"][exchange][side]["price"] = price
        _save(data)


def get_manual_ad(exchange: str, side: str) -> dict:
    data = _load()
    return data.get("manual_ads", {}).get(exchange, {}).get(side, {})
def delete_manual_ad(exchange: str, side: str):
    with _lock:
        data = _load()
        if exchange in data.get("manual_ads", {}) and side in data["manual_ads"][exchange]:
            del data["manual_ads"][exchange][side]
            _save(data)

def is_order_seen(order_id: str) -> bool:
    data = _load()
    return order_id in data.get("seen_order_ids", [])


def mark_order_seen(order_id: str):
    with _lock:
        data = _load()
        seen = data.setdefault("seen_order_ids", [])
        seen.append(order_id)
        # ограничим список последними 2000 записями, чтобы файл не рос бесконечно
        data["seen_order_ids"] = seen[-2000:]
        _save(data)
