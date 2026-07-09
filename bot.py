"""
Мультибиржевый P2P-бот.

Для КЛИЕНТОВ (любой человек может писать боту):
    /start -> кнопки "Купить" / "Продать" -> кнопки бирж с ценой -> ссылка на объявление

Для ТЕБЯ (администратора, только твой ADMIN_CHAT_ID):
    /setup                                 - мастер настройки кнопками: выбрать биржу -> сторону ->
                                              изменить ссылку/цену или удалить объявление,
                                              на каждом шаге есть кнопка "Назад"
    /setlink <биржа> <buy|sell> <ссылка>   - задать ссылку вручную командой (запасной вариант)
    /setprice <биржа> <buy|sell> <цена>    - задать цену вручную командой (запасной вариант)
    /status                                - показать все биржи и их текущие объявления

Фоновая проверка (только для бирж с API - Bybit, Bitget):
    каждые 30 секунд бот проверяет новые ордера и присылает тебе уведомление
    с ником клиента и суммой.
"""

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
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
# Постоянная клавиатура снизу экрана - видна пользователю всегда
MAIN_KEYBOARD = ReplyKeyboardMarkup([["▶️ Старт", "🔄 Рестарт"]], resize_keyboard=True)


async def build_rates_text() -> str:
    """Собирает текст с текущими курсами всех бирж сразу (Купить и Продать)."""
    lines = ["📊 Актуальные курсы USDT/RUB:\n"]
    for exchange in EXCHANGES.values():
        try:
            buy_ad = exchange.get_my_ad(side="buy", token=config.TOKEN, currency=config.CURRENCY)
        except Exception:
            buy_ad = None
        try:
            sell_ad = exchange.get_my_ad(side="sell", token=config.TOKEN, currency=config.CURRENCY)
        except Exception:
            sell_ad = None

        buy_price = f"{buy_ad['price']:.2f} ₽" if buy_ad and buy_ad.get("price") is not None else "—"
        sell_price = f"{sell_ad['price']:.2f} ₽" if sell_ad and sell_ad.get("price") is not None else "—"
        lines.append(f"{exchange.name}: Купить {buy_price} | Продать {sell_price}")

    return "\n".join(lines)


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает курсы всех бирж + кнопки Купить/Продать. Вызывается из /start и кнопок Старт/Рестарт."""
    pending_setup.pop(update.effective_chat.id, None)  # сбрасываем зависшие состояния мастера, если были

    rates_text = await build_rates_text()

    inline_buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💵 Купить USDT", callback_data="side:buy"),
            InlineKeyboardButton("💰 Продать USDT", callback_data="side:sell"),
        ]
    ])

    # Два сообщения: одно держит постоянную клавиатуру снизу, второе - курсы и инлайн-кнопки
    await update.message.reply_text("Меню:", reply_markup=MAIN_KEYBOARD)
    await update.message.reply_text(rates_text, reply_markup=inline_buttons)

# Состояние пошагового мастера /setup для админа:
# pending_setup[chat_id] = {"exchange": "htx", "side": "buy", "step": "link"|"price"}
pending_setup: dict[int, dict] = {}


def is_admin(update: Update) -> bool:
    return update.effective_chat.id == config.ADMIN_CHAT_ID


# ---------------------------------------------------------------------------
# КЛИЕНТСКИЙ FLOW
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_main_menu(update, context)


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

        if not ad or not ad.get("link"):
            continue  # у этой биржи нет ссылки - клиенту показывать нечего
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
    elif data.startswith("setup_ex:"):
        _, exchange_key = data.split(":", 1)
        await show_side_menu(query, exchange_key)
    elif data.startswith("setup_side:"):
        _, exchange_key, side = data.split(":", 2)
        pending_setup.pop(query.message.chat_id, None)  # выходим из ввода текста, если были в нём
        await show_action_menu(query, exchange_key, side)
    elif data.startswith("setup_field:"):
        _, exchange_key, side, field = data.split(":", 3)
        await ask_for_field(query, exchange_key, side, field)
    elif data.startswith("setup_delete_ask:"):
        _, exchange_key, side = data.split(":", 2)
        await ask_delete_confirm(query, exchange_key, side)
    elif data.startswith("setup_delete_yes:"):
        _, exchange_key, side = data.split(":", 2)
        storage.delete_manual_ad(exchange_key, side)
        await show_action_menu(query, exchange_key, side)
    elif data == "setup_back_ex":
        pending_setup.pop(query.message.chat_id, None)
        await show_exchange_menu(query)
    elif data.startswith("setup_back_side:"):
        _, exchange_key = data.split(":", 1)
        await show_side_menu(query, exchange_key)
    elif data.startswith("setup_back_action:"):
        _, exchange_key, side = data.split(":", 2)
        pending_setup.pop(query.message.chat_id, None)
        await show_action_menu(query, exchange_key, side)


# ---------------------------------------------------------------------------
# АДМИНСКИЕ КОМАНДЫ (только ADMIN_CHAT_ID)
# ---------------------------------------------------------------------------

async def setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Точка входа в мастер настройки. Дальше всё происходит через кнопки."""
    if not is_admin(update):
        return

    manual_keys = [key for key, ex in EXCHANGES.items() if not ex.has_api]
    if not manual_keys:
        await update.message.reply_text("Нет ручных бирж для настройки (все подключены через API).")
        return

    buttons = [
        [InlineKeyboardButton(EXCHANGES[key].name, callback_data=f"setup_ex:{key}")]
        for key in manual_keys
    ]
    await update.message.reply_text(
        "Какую биржу настраиваем?",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


def _ad_status_line(exchange_key: str, side: str) -> str:
    """Короткое текстовое описание текущего состояния объявления для меню."""
    ad = storage.get_manual_ad(exchange_key, side)
    if not ad:
        return "не настроено"
    parts = []
    parts.append("ссылка ✅" if ad.get("url") else "ссылка ❌")
    parts.append(f"цена {ad['price']:.2f} ₽" if ad.get("price") is not None else "цена ❌")
    return ", ".join(parts)


async def show_exchange_menu(query):
    manual_keys = [key for key, ex in EXCHANGES.items() if not ex.has_api]
    buttons = [
        [InlineKeyboardButton(EXCHANGES[key].name, callback_data=f"setup_ex:{key}")]
        for key in manual_keys
    ]
    await query.edit_message_text("Какую биржу настраиваем?", reply_markup=InlineKeyboardMarkup(buttons))


async def show_side_menu(query, exchange_key: str):
    name = EXCHANGES[exchange_key].name
    buttons = [
        [InlineKeyboardButton(
            f"Покупка ({_ad_status_line(exchange_key, 'buy')})",
            callback_data=f"setup_side:{exchange_key}:buy",
        )],
        [InlineKeyboardButton(
            f"Продажа ({_ad_status_line(exchange_key, 'sell')})",
            callback_data=f"setup_side:{exchange_key}:sell",
        )],
        [InlineKeyboardButton("⬅️ Назад", callback_data="setup_back_ex")],
    ]
    await query.edit_message_text(f"{name}: какую сторону настраиваем?", reply_markup=InlineKeyboardMarkup(buttons))


async def show_action_menu(query, exchange_key: str, side: str):
    name = EXCHANGES[exchange_key].name
    ad = storage.get_manual_ad(exchange_key, side)
    link_text = ad.get("url", "не задана")
    price_text = f"{ad['price']:.2f} ₽" if ad.get("price") is not None else "не задана"

    buttons = [
        [InlineKeyboardButton("✏️ Изменить ссылку", callback_data=f"setup_field:{exchange_key}:{side}:link")],
        [InlineKeyboardButton("💰 Изменить цену", callback_data=f"setup_field:{exchange_key}:{side}:price")],
        [InlineKeyboardButton("🗑 Удалить объявление", callback_data=f"setup_delete_ask:{exchange_key}:{side}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"setup_back_side:{exchange_key}")],
    ]
    await query.edit_message_text(
        f"{name} - {SIDE_LABELS[side]}\n\nСсылка: {link_text}\nЦена: {price_text}",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def ask_for_field(query, exchange_key: str, side: str, field: str):
    chat_id = query.message.chat_id
    pending_setup[chat_id] = {"exchange": exchange_key, "side": side, "field": field}

    prompt = "Пришли ссылку на объявление обычным сообщением." if field == "link" else "Пришли цену числом, например 95.50"
    buttons = [[InlineKeyboardButton("⬅️ Назад (отмена)", callback_data=f"setup_back_action:{exchange_key}:{side}")]]
    await query.edit_message_text(prompt, reply_markup=InlineKeyboardMarkup(buttons))


async def ask_delete_confirm(query, exchange_key: str, side: str):
    name = EXCHANGES[exchange_key].name
    buttons = [
        [
            InlineKeyboardButton("✅ Да, удалить", callback_data=f"setup_delete_yes:{exchange_key}:{side}"),
            InlineKeyboardButton("❌ Отмена", callback_data=f"setup_side:{exchange_key}:{side}"),
        ]
    ]
    await query.edit_message_text(
        f"Точно удалить объявление {name} ({SIDE_LABELS[side]})? Ссылка и цена будут стёрты.",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def handle_setup_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ловит текстовые ответы во время прохождения мастера /setup. Обычные сообщения не трогает."""
    chat_id = update.effective_chat.id
    state = pending_setup.get(chat_id)
    if state is None:
        return  # мастер не запущен - ничего не делаем, это не наша забота

    exchange_key, side, field = state["exchange"], state["side"], state["field"]
    text = update.message.text.strip()

    if field == "link":
        storage.set_manual_ad(exchange_key, side, url=text)
        pending_setup.pop(chat_id, None)
        await update.message.reply_text(f"Ссылка сохранена: {text}")
    else:  # field == "price"
        try:
            price = float(text.replace(",", "."))
        except ValueError:
            await update.message.reply_text("Это не похоже на число. Пришли цену ещё раз, например 95.50")
            return
        storage.set_manual_ad(exchange_key, side, price=price)
        pending_setup.pop(chat_id, None)
        await update.message.reply_text(f"Цена сохранена: {price:.2f} ₽")

    # Показываем актуальное меню действий отдельным сообщением
    # (предыдущее сообщение с кнопками уже было отредактировано в "пришли ссылку/цену" и текстом ответить на него нельзя)
    name = EXCHANGES[exchange_key].name
    ad = storage.get_manual_ad(exchange_key, side)
    link_text = ad.get("url", "не задана")
    price_text = f"{ad['price']:.2f} ₽" if ad.get("price") is not None else "не задана"
    buttons = [
        [InlineKeyboardButton("✏️ Изменить ссылку", callback_data=f"setup_field:{exchange_key}:{side}:link")],
        [InlineKeyboardButton("💰 Изменить цену", callback_data=f"setup_field:{exchange_key}:{side}:price")],
        [InlineKeyboardButton("🗑 Удалить объявление", callback_data=f"setup_delete_ask:{exchange_key}:{side}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"setup_back_side:{exchange_key}")],
    ]
    await update.message.reply_text(
        f"{name} - {SIDE_LABELS[side]}\n\nСсылка: {link_text}\nЦена: {price_text}",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


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
    application.add_handler(CommandHandler("setup", setup))
    application.add_handler(CommandHandler("setlink", setlink))
    application.add_handler(CommandHandler("setprice", setprice))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.Text(["▶️ Старт", "🔄 Рестарт"]), show_main_menu))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_setup_text))

    application.job_queue.run_repeating(check_new_orders, interval=config.CHECK_ORDERS_INTERVAL, first=10)

    print("Мультибиржевый P2P-бот запущен! Ctrl+C для остановки.")
    application.run_polling()


if __name__ == "__main__":
    main()
