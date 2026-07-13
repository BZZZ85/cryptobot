"""
Хранилище на PostgreSQL (Railway Postgres plugin).
Таблицы создаются автоматически при первом запуске - init_db() вызывается из bot.py.
"""

import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
DATABASE_URL = os.getenv("DATABASE_URL")


@contextmanager
def get_conn():
    conn = psycopg.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS manual_ads (
                    exchange TEXT NOT NULL,
                    side TEXT NOT NULL,
                    url TEXT,
                    price DOUBLE PRECISION,
                    PRIMARY KEY (exchange, side)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS seen_orders (
                    order_id TEXT PRIMARY KEY
                )
            """)
            cur.execute("ALTER TABLE seen_orders ADD COLUMN IF NOT EXISTS exchange TEXT")
            cur.execute("ALTER TABLE seen_orders ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT now()")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS clicks (
                    id SERIAL PRIMARY KEY,
                    exchange TEXT,
                    side TEXT,
                    username TEXT,
                    created_at TIMESTAMP DEFAULT now()
                )
            """)
            cur.execute("ALTER TABLE clicks ADD COLUMN IF NOT EXISTS chat_id BIGINT")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS clients (
                    chat_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_seen TIMESTAMP DEFAULT now()
                )
            """)
            cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS manual_discount_percent DOUBLE PRECISION DEFAULT 0")
            cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS full_name TEXT")
            cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS exchange_nickname TEXT")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rate_history (
                    id SERIAL PRIMARY KEY,
                    buy DOUBLE PRECISION,
                    sell DOUBLE PRECISION,
                    recorded_at TIMESTAMP DEFAULT now()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS price_alerts (
                    id SERIAL PRIMARY KEY,
                    chat_id BIGINT NOT NULL,
                    side TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    threshold DOUBLE PRECISION NOT NULL,
                    active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT now()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS reviews (
                    id SERIAL PRIMARY KEY,
                    chat_id BIGINT NOT NULL,
                    exchange TEXT,
                    side TEXT,
                    rating INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT now()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

def set_manual_ad(exchange: str, side: str, url: str = None, price: float = None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO manual_ads (exchange, side, url, price)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (exchange, side) DO UPDATE SET
                    url = COALESCE(EXCLUDED.url, manual_ads.url),
                    price = COALESCE(EXCLUDED.price, manual_ads.price)
            """, (exchange, side, url, price))


def get_manual_ad(exchange: str, side: str) -> dict:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT url, price FROM manual_ads WHERE exchange=%s AND side=%s",
                (exchange, side),
            )
            row = cur.fetchone()
            return dict(row) if row else {}


def delete_manual_ad(exchange: str, side: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM manual_ads WHERE exchange=%s AND side=%s", (exchange, side))


def delete_manual_price(exchange: str, side: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE manual_ads SET price=NULL WHERE exchange=%s AND side=%s", (exchange, side))


def is_order_seen(order_id: str) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM seen_orders WHERE order_id=%s", (order_id,))
            return cur.fetchone() is not None


def mark_order_seen(order_id: str, exchange: str = None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO seen_orders (order_id, exchange) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (order_id, exchange),
            )


def get_conversion_stats(days: int = 7) -> list:
    """Клики vs подтверждённые ордера за период. Считается только для бирж с API (Bybit/Bitget) -
    для ручных бирж (HTX/MEXC) у нас нет данных о реально совершённых сделках, только клики."""
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT exchange, COUNT(*) as clicks
                FROM clicks
                WHERE created_at > now() - (%s || ' days')::interval
                GROUP BY exchange
            """, (days,))
            clicks = {row["exchange"]: row["clicks"] for row in cur.fetchall()}

            cur.execute("""
                SELECT exchange, COUNT(*) as orders
                FROM seen_orders
                WHERE created_at > now() - (%s || ' days')::interval AND exchange IS NOT NULL
                GROUP BY exchange
            """, (days,))
            orders = {row["exchange"]: row["orders"] for row in cur.fetchall()}

    result = []
    for ex in sorted(set(clicks) | set(orders)):
        c, o = clicks.get(ex, 0), orders.get(ex, 0)
        result.append({
            "exchange": ex,
            "clicks": c,
            "orders": o,
            "conversion_pct": round(o / c * 100, 1) if c else None,
        })
    return result


def record_click(exchange: str, side: str, username: str, chat_id: int = None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO clicks (exchange, side, username, chat_id) VALUES (%s, %s, %s, %s)",
                (exchange, side, username, chat_id),
            )


def get_user_clicks(chat_id: int, limit: int = 10) -> list:
    """История сделок конкретного клиента для вкладки Профиль."""
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT exchange, side, created_at
                FROM clicks
                WHERE chat_id=%s
                ORDER BY created_at DESC
                LIMIT %s
            """, (chat_id, limit))
            return [dict(row) for row in cur.fetchall()]


def get_total_clicks_count() -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM clicks")
            return cur.fetchone()[0]


def get_click_stats() -> list:
    """Возвращает [{"exchange":, "side":, "count":}, ...], отсортировано по убыванию."""
    with get_conn() as conn:
       with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT exchange, side, COUNT(*) as count
                FROM clicks
                GROUP BY exchange, side
                ORDER BY count DESC
            """)
            return [dict(row) for row in cur.fetchall()]
def record_client(chat_id: int, username: str, referred_by: int = None) -> bool:
    """Возвращает True, если это новый клиент (первое обращение)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO clients (chat_id, username, referred_by) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING RETURNING chat_id",
                (chat_id, username, referred_by),
            )
            return cur.fetchone() is not None


def get_referral_count(chat_id: int) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM clients WHERE referred_by=%s", (chat_id,))
            return cur.fetchone()[0]


def get_client_profile(chat_id: int) -> dict | None:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT full_name, exchange_nickname FROM clients WHERE chat_id=%s", (chat_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def update_client_profile(chat_id: int, full_name: str = None, exchange_nickname: str = None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE clients SET full_name=%s, exchange_nickname=%s WHERE chat_id=%s",
                (full_name, exchange_nickname, chat_id),
            )


def get_user_deal_count(chat_id: int) -> int:
    """Сколько раз клиент нажимал 'открыть сделку' - используется для программы лояльности."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM clicks WHERE chat_id=%s", (chat_id,))
            return cur.fetchone()[0]


def get_all_client_ids() -> list:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT chat_id FROM clients")
            return [row[0] for row in cur.fetchall()]
def record_rate_snapshot(buy: float, sell: float):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO rate_history (buy, sell) VALUES (%s, %s)",
                (buy, sell),
            )


def get_rate_history(hours: int = 24) -> list:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT buy, sell, recorded_at
                FROM rate_history
                WHERE recorded_at > now() - (%s || ' hours')::interval
                ORDER BY recorded_at ASC
            """, (hours,))
            return [dict(row) for row in cur.fetchall()]


def get_click_history(days: int = 7) -> list:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT DATE(created_at) as day, COUNT(*) as count
                FROM clicks
                WHERE created_at > now() - (%s || ' days')::interval
                GROUP BY DATE(created_at)
                ORDER BY day ASC
            """, (days,))
            return [{"day": str(row["day"]), "count": row["count"]} for row in cur.fetchall()]


# --- Ценовые алерты ---

def create_price_alert(chat_id: int, side: str, direction: str, threshold: float) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO price_alerts (chat_id, side, direction, threshold) VALUES (%s, %s, %s, %s) RETURNING id",
                (chat_id, side, direction, threshold),
            )
            return cur.fetchone()[0]


def get_user_alerts(chat_id: int) -> list:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT id, side, direction, threshold FROM price_alerts WHERE chat_id=%s AND active=TRUE ORDER BY id DESC",
                (chat_id,),
            )
            return [dict(row) for row in cur.fetchall()]


def get_all_active_alerts() -> list:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT id, chat_id, side, direction, threshold FROM price_alerts WHERE active=TRUE")
            return [dict(row) for row in cur.fetchall()]


def cancel_alert(alert_id: int, chat_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE price_alerts SET active=FALSE WHERE id=%s AND chat_id=%s", (alert_id, chat_id))


def deactivate_alert(alert_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE price_alerts SET active=FALSE WHERE id=%s", (alert_id,))


# --- Отзывы ---

def record_review(chat_id: int, exchange: str, side: str, rating: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO reviews (chat_id, exchange, side, rating) VALUES (%s, %s, %s, %s)",
                (chat_id, exchange, side, rating),
            )


def get_review_stats() -> dict:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT COUNT(*) as count, AVG(rating) as avg_rating FROM reviews")
            row = cur.fetchone()
            return {"count": row["count"], "avg_rating": round(row["avg_rating"], 2) if row["avg_rating"] else None}


# --- Настройки (наценка и т.п.) ---

def get_setting(key: str, default: str = None) -> str:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM settings WHERE key=%s", (key,))
            row = cur.fetchone()
            return row[0] if row else default


def set_setting(key: str, value: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (key, str(value)),
            )
def set_manual_discount(chat_id: int, percent: float):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE clients SET manual_discount_percent=%s WHERE chat_id=%s",
                (percent, chat_id),
            )


def get_manual_discount(chat_id: int) -> float:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT manual_discount_percent FROM clients WHERE chat_id=%s", (chat_id,))
            row = cur.fetchone()
            return float(row[0]) if row and row[0] else 0.0


def find_chat_id_by_username(username: str) -> int | None:
    """Ищет chat_id клиента по его @username (без @)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT chat_id FROM clients WHERE username ILIKE %s", (f"@{username}",))
            row = cur.fetchone()
            return row[0] if row else None
