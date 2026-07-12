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
            cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS referred_by BIGINT")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rate_history (
                    id SERIAL PRIMARY KEY,
                    buy DOUBLE PRECISION,
                    sell DOUBLE PRECISION,
                    recorded_at TIMESTAMP DEFAULT now()
                )
            """)
            cur.execute("ALTER TABLE clicks ADD COLUMN IF NOT EXISTS completed BOOLEAN DEFAULT FALSE")
            cur.execute("ALTER TABLE clicks ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS price_alerts (
                    id SERIAL PRIMARY KEY,
                    chat_id BIGINT NOT NULL,
                    side TEXT NOT NULL,
                    threshold DOUBLE PRECISION NOT NULL,
                    active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT now()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS reviews (
                    id SERIAL PRIMARY KEY,
                    click_id INTEGER,
                    chat_id BIGINT,
                    username TEXT,
                    rating INTEGER NOT NULL,
                    comment TEXT,
                    created_at TIMESTAMP DEFAULT now()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bot_settings (
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


def mark_order_seen(order_id: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO seen_orders (order_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (order_id,),
            )


def record_click(exchange: str, side: str, username: str, chat_id: int = None) -> int:
    """Возвращает id созданного клика - нужен для кнопки 'Сделка состоялась'."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO clicks (exchange, side, username, chat_id) VALUES (%s, %s, %s, %s) RETURNING id",
                (exchange, side, username, chat_id),
            )
            return cur.fetchone()[0]


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
def mark_click_completed(click_id: int) -> dict | None:
    """Отмечает клик как реально состоявшуюся сделку. Возвращает данные клика для дальнейшего запроса отзыва."""
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "UPDATE clicks SET completed=TRUE, completed_at=now() WHERE id=%s AND completed=FALSE RETURNING id, exchange, side, chat_id, username",
                (click_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def get_conversion_stats() -> dict:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT COUNT(*) as total, COUNT(*) FILTER (WHERE completed) as completed FROM clicks")
            row = cur.fetchone()
            total = row["total"] or 0
            completed = row["completed"] or 0
            rate = round(completed / total * 100, 1) if total else 0.0
            return {"total_clicks": total, "completed_deals": completed, "conversion_rate": rate}


def add_price_alert(chat_id: int, side: str, threshold: float) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO price_alerts (chat_id, side, threshold) VALUES (%s, %s, %s) RETURNING id",
                (chat_id, side, threshold),
            )
            return cur.fetchone()[0]


def get_active_alerts() -> list:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT id, chat_id, side, threshold FROM price_alerts WHERE active=TRUE")
            return [dict(row) for row in cur.fetchall()]


def deactivate_alert(alert_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE price_alerts SET active=FALSE WHERE id=%s", (alert_id,))


def get_user_alerts(chat_id: int) -> list:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT id, side, threshold FROM price_alerts WHERE chat_id=%s AND active=TRUE ORDER BY id DESC",
                (chat_id,),
            )
            return [dict(row) for row in cur.fetchall()]


def add_review(chat_id: int, username: str, rating: int, comment: str = None, click_id: int = None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO reviews (click_id, chat_id, username, rating, comment) VALUES (%s, %s, %s, %s, %s)",
                (click_id, chat_id, username, rating, comment),
            )


def get_review_stats() -> dict:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT COUNT(*) as count, AVG(rating) as avg_rating FROM reviews")
            row = cur.fetchone()
            count = row["count"] or 0
            avg = round(float(row["avg_rating"]), 1) if row["avg_rating"] else None
            return {"count": count, "avg_rating": avg}


def get_recent_reviews(limit: int = 10) -> list:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT username, rating, comment, created_at FROM reviews WHERE comment IS NOT NULL ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]


def get_markup_percent() -> float:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM bot_settings WHERE key='markup_percent'")
            row = cur.fetchone()
            return float(row[0]) if row else 5.0


def set_markup_percent(value: float):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO bot_settings (key, value) VALUES ('markup_percent', %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (str(value),),
            )
