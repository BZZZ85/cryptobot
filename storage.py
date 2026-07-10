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
            cur.execute("""
                CREATE TABLE IF NOT EXISTS clients (
                    chat_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_seen TIMESTAMP DEFAULT now()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rate_history (
                    id SERIAL PRIMARY KEY,
                    buy DOUBLE PRECISION,
                    sell DOUBLE PRECISION,
                    recorded_at TIMESTAMP DEFAULT now()
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


def record_click(exchange: str, side: str, username: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO clicks (exchange, side, username) VALUES (%s, %s, %s)",
                (exchange, side, username),
            )


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
def record_client(chat_id: int, username: str) -> bool:
    """Возвращает True, если это новый клиент (первое обращение)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO clients (chat_id, username) VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING chat_id",
                (chat_id, username),
            )
            return cur.fetchone() is not None


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
