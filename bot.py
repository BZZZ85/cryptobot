"""
Мультибиржевый P2P-бот.

Для КЛИЕНТОВ (любой человек может писать боту):
    /start -> кнопки "Купить" / "Продать" -> кнопки бирж с ценой -> ссылка на объявление

Для ТЕБЯ (администратора, только твой ADMIN_CHAT_ID):
    /setlink <биржа> <buy|sell> <ссылка>   - задать ссылку для ручной биржи (htx, mexc)
    /setprice <биржа> <buy|sell> <цена>    - задать цену для ручной биржи
    /status                                - показать все биржи и их текущие объявления

Фоновая проверка (только для бирж с API - Bybit, Bitget):
    каждые 30 секунд бот проверяет новые ордера и присылает тебе уведомление
    с ником клиента и суммой.
"""

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

import config
import storage

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

EXCHANGES = config.build_exchanges()

SIDE_LABELS = {"buy": "Купить", "sell": "Продать"}


def is_admin(update: Update) -> bool:
    return update.effective_chat.id == config.ADMIN_CHAT_ID


# ---------------------------------------------------------------------------
# КЛИЕНТСКИЙ FLOW
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💵 Купить USDT", callback_data="side:buy"),
            InlineKeyboardButton("💰 Продать USDT", callback_data="side:sell"),
        ]
    ])
    await update.message.reply_text(
        "Привет! Что хочешь сделать?",
        reply_markup=keyboard,
    )


async def handle_side_choice(query, side: str):
    """Показывает клиенту список бирж с ценой для выбранной стороны сделки."""
    buttons = []
    text_lines = [f"Выбери биржу ({SIDE_LABELS[side]} USDT/RUB):"]

    for key, exchange in EXCHANGES.items():
        try:
            ad = exchange.get_my_ad(side=side, token=config.TOKEN, currency=config.CURRENCY)
        except Exception as e:
            logger.error(f"{exchange.name}: ошибка получения объявления - {e}")
            continue

        if not ad:
            continue  # у этой биржи нет активного объявления такого типа - не показываем её

        price_text = f" ~{ad['price']:.2f} ₽" if ad.get("price") else ""
        label = f"{exchange.name}{price_text}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"go:{key}:{side}")])

    if not buttons:
        await query.edit_message_text(
            "Пока нет доступных объявлений для этой операции. Попробуй позже."
        )
        return

    await query.edit_message_text("\n".join(text_lines), reply_markup=InlineKeyboardMarkup(buttons))


async def handle_exchange_choice(query, exchange_key: str, side: str, context: ContextTypes.DEFAULT_TYPE):
    """Отдаёт клиенту ссылку на объявление и уведомляет админа о переходе."""
    exchange = EXCHANGES.get(exchange_key)
    if exchange is None:
        await query.edit_message_text("Эта биржа сейчас недоступна.")
        return

    ad = exchange.get_my_ad(side=side, token=config.TOKEN, currency=config.CURRENCY)
    if not ad or not ad.get("link"):
        await query.edit_message_text("Ссылка временно недоступна, попробуй позже.")
        return

    await query.edit_message_text(
        f"{SIDE_LABELS[side]} USDT/RUB на {exchange.name}"
        + (f" по цене ~{ad['price']:.2f} ₽" if ad.get("price") else "")
        + f":\n{ad['link']}\n\n"
        f"Открой ссылку в приложении/сайте {exchange.name} и оформи сделку там."
    )

    # Уведомляем админа, что клиент перешёл по ссылке - особенно важно для "ручных" бирж,
    # где бот не может сам узнать о реальном ордере на бирже.
    user = query.from_user
    username = f"@{user.username}" if user.username else user.full_name
    try:
        await context.bot.send_message(
            chat_id=config.ADMIN_CHAT_ID,
            text=(
                f"👉 Клиент {username} выбрал: {SIDE_LABELS[side]} USDT/RUB на {exchange.name}"
                + (f" (~{ad['price']:.2f} ₽)" if ad.get("price") else "")
            ),
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить админа: {e}")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("side:"):
        _, side = data.split(":", 1)
        await handle_side_choice(query, side)
    elif data.startswith("go:"):
        _, exchange_key, side = data.split(":", 2)
        await handle_exchange_choice(query, exchange_key, side, context)


# ---------------------------------------------------------------------------
# АДМИНСКИЕ КОМАНДЫ (только ADMIN_CHAT_ID)
# ---------------------------------------------------------------------------

async def setlink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if len(context.args) < 3:
        await update.message.reply_text("Формат: /setlink <биржа> <buy|sell> <ссылка>\nПример: /setlink htx buy https://...")
        return

    exchange_key, side, url = context.args[0].lower(), context.args[1].lower(), context.args[2]
    if exchange_key not in EXCHANGES or EXCHANGES[exchange_key].has_api:
        await update.message.reply_text(
            "Эта команда только для 'ручных' бирж (например htx, mexc)."
        )
        return
    if side not in ("buy", "sell"):
        await update.message.reply_text("Сторона должна быть buy или sell.")
        return

    storage.set_manual_ad(exchange_key, side, url=url)
    await update.message.reply_text(f"Ссылка для {exchange_key} ({side}) сохранена.")


async def setprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if len(context.args) < 3:
        await update.message.reply_text("Формат: /setprice <биржа> <buy|sell> <цена>")
        return

    exchange_key, side = context.args[0].lower(), context.args[1].lower()
    try:
        price = float(context.args[2])
    except ValueError:
        await update.message.reply_text("Цена должна быть числом.")
        return

    if exchange_key not in EXCHANGES or EXCHANGES[exchange_key].has_api:
        await update.message.reply_text("Эта команда только для 'ручных' бирж (например htx, mexc).")
        return

    storage.set_manual_ad(exchange_key, side, price=price)
    await update.message.reply_text(f"Цена для {exchange_key} ({side}) обновлена: {price:.2f} ₽")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    lines = ["Статус бирж:"]
    for key, exchange in EXCHANGES.items():
        mode = "API" if exchange.has_api else "ручной режим"
        lines.append(f"\n{exchange.name} ({mode}):")
        for side in ("buy", "sell"):
            try:
                ad = exchange.get_my_ad(side=side, token=config.TOKEN, currency=config.CURRENCY)
            except Exception as e:
                lines.append(f"  {SIDE_LABELS[side]}: ошибка - {e}")
                continue
            if ad:
                lines.append(f"  {SIDE_LABELS[side]}: цена {ad.get('price')}")
            else:
                lines.append(f"  {SIDE_LABELS[side]}: объявление не найдено")

    await update.message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# ФОНОВАЯ ПРОВЕРКА НОВЫХ ОРДЕРОВ (только биржи с API)
# ---------------------------------------------------------------------------

async def check_new_orders(context: ContextTypes.DEFAULT_TYPE):
    for key, exchange in EXCHANGES.items():
        if not exchange.has_api:
            continue
        try:
            orders = exchange.get_new_orders()
        except Exception as e:
            logger.error(f"{exchange.name}: ошибка проверки ордеров - {e}")
            continue

        for order in orders:
            if storage.is_order_seen(order["order_id"]):
                continue
            storage.mark_order_seen(order["order_id"])

            text = (
                f"🔔 Новый ордер на {exchange.name}!\n"
                f"Ник: {order['nick']}\n"
                f"Сумма: {order['amount']} {order['currency']}\n"
                f"Количество: {order['quantity']} {order['token']}"
            )
            await context.bot.send_message(chat_id=config.ADMIN_CHAT_ID, text=text)


# ---------------------------------------------------------------------------
# ЗАПУСК
# ---------------------------------------------------------------------------

def main():
    if not config.TELEGRAM_BOT_TOKEN or not config.ADMIN_CHAT_ID:
        print("ОШИБКА: заполни TELEGRAM_BOT_TOKEN и ADMIN_CHAT_ID в .env")
        return

    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("setlink", setlink))
    application.add_handler(CommandHandler("setprice", setprice))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CallbackQueryHandler(handle_callback))

    application.job_queue.run_repeating(check_new_orders, interval=config.CHECK_ORDERS_INTERVAL, first=10)

    print("Мультибиржевый P2P-бот запущен! Ctrl+C для остановки.")
    application.run_polling()


if __name__ == "__main__":
    main()
