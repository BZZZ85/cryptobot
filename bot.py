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
from telegram.ext import Defaults
import os

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
EXCHANGE_EMOJI = {
    "bybit": "🟨",
    "bitget": "🔴",
    "htx": "🟦",
    "mexc": "🟩",
}
BANNER_PATH = os.path.join(os.path.dirname(__file__), "assets", "banner.png")
# Постоянная клавиатура снизу экрана - видна пользователю всегда
MAIN_KEYBOARD = ReplyKeyboardMarkup([["▶️ Старт", "🔄 Рестарт"], ["❓ Помощь"]], resize_keyboard=True)
ADMIN_KEYBOARD = ReplyKeyboardMarkup(
    [["▶️ Старт", "🔄 Рестарт"], ["⚙️ Настройка", "❓ Помощь"]],
    resize_keyboard=True,
)


async def build_rates_text() -> str:
    """Собирает красиво оформленный текст с курсами всех бирж (HTML-разметка)."""
    lines = ["📊 <b>Актуальные курсы USDT/RUB</b>", "━━━━━━━━━━━━━━━"]
    for key, exchange in EXCHANGES.items():
        try:
            buy_ad = exchange.get_my_ad(side="buy", token=config.TOKEN, currency=config.CURRENCY)
        except Exception:
            buy_ad = None
        try:
            sell_ad = exchange.get_my_ad(side="sell", token=config.TOKEN, currency=config.CURRENCY)
        except Exception:
            sell_ad = None

        buy_price = f"<code>{buy_ad['price']:.2f} ₽</code>" if buy_ad and buy_ad.get("price") is not None else "—"
        sell_price = f"<code>{sell_ad['price']:.2f} ₽</code>" if sell_ad and sell_ad.get("price") is not None else "—"
        lines.append(f"\n{EXCHANGE_EMOJI.get(key, '🏦')} <b>{exchange.name}</b>")
        lines.append(f"🟢 Купить: {buy_price}")
        lines.append(f"🔴 Продать: {sell_price}")

    return "\n".join(lines)


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает курсы всех бирж + кнопки Купить/Продать. Вызывается из /start и кнопок Старт/Рестарт."""
    pending_setup.pop(update.effective_chat.id, None)  # сбрасываем зависшие состояния мастера, если были
    user = update.effective_user
    username = f"@{user.username}" if user.username else user.full_name
    is_new_client = storage.record_client(update.effective_chat.id, username)

    if is_new_client and config.WELCOME_STICKER_ID:
        try:
            await update.message.reply_sticker(config.WELCOME_STICKER_ID)
        except Exception as e:
            logger.error(f"Не удалось отправить приветственный стикер: {e}")
    

    rates_text = await build_rates_text()

    inline_buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💵 Купить USDT", callback_data="side:buy", style="success"),
            InlineKeyboardButton("💰 Продать USDT", callback_data="side:sell", style="danger"),
        ]
    ])

    # Два сообщения: одно держит постоянную клавиатуру снизу, второе - курсы и инлайн-кнопки
    keyboard = ADMIN_KEYBOARD if is_admin(update) else MAIN_KEYBOARD
    if os.path.exists(BANNER_PATH):
        with open(BANNER_PATH, "rb") as photo:
            await update.message.reply_photo(
                photo=photo,
                caption="🤖 <b>USDT/RUB Обменник</b>\nБыстро и по актуальному курсу",
            )

    await update.message.reply_text("👇 Что делаем дальше?", reply_markup=keyboard)
    await update.message.reply_text(rates_text, reply_markup=inline_buttons)

# Состояние пошагового мастера /setup для админа:
# pending_setup[chat_id] = {"exchange": "htx", "side": "buy", "step": "link"|"price"}
pending_setup: dict[int, dict] = {}
pending_amount_request: dict[int, dict] = {}
pending_broadcast: dict[int, str] = {}
async def show_side_selection(query):
    inline_buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💵 Купить USDT", callback_data="side:buy", style="success"),
            InlineKeyboardButton("💰 Продать USDT", callback_data="side:sell", style="danger"),
        ]
    ])
    await query.edit_message_text("👇 Что делаем дальше?", reply_markup=inline_buttons)

def is_admin(update: Update) -> bool:
    return update.effective_chat.id in config.ADMIN_CHAT_IDS


# ---------------------------------------------------------------------------
# КЛИЕНТСКИЙ FLOW
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_main_menu(update, context)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "❓ <b>Часто задаваемые вопросы</b>\n"
        "━━━━━━━━━━━━━━━\n\n"
        "<b>Как купить/продать USDT?</b>\n"
        "Нажми ▶️ Старт → выбери Купить или Продать → выбери биржу → перейди по ссылке "
        "и оформи сделку на самой бирже.\n\n"
        "<b>Курс не совпадает с тем, что я вижу на бирже?</b>\n"
        "Курс обновляется регулярно, но может отличаться на пару минут — актуальная цена "
        "всегда видна прямо в объявлении на самой бирже.\n\n"
        "<b>Проблема со сделкой или вопрос?</b>\n"
        f"Пиши напрямую: {config.SUPPORT_CONTACT}"
    )
    await update.message.reply_text(text)
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
        emoji = EXCHANGE_EMOJI.get(key, "🏦")
        label = f"{emoji} {exchange.name}{price_text}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"go:{key}:{side}")])

    if not buttons:
        back_only = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]])
        await query.edit_message_text(
            "Пока нет доступных объявлений для этой операции. Попробуй позже.",
            reply_markup=back_only,
        )
        return

    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")])
    await query.edit_message_text("\n".join(text_lines), reply_markup=InlineKeyboardMarkup(buttons))

async def deliver_link(context: ContextTypes.DEFAULT_TYPE, chat_id: int, username: str, exchange_key: str, side: str, amount: str = None) -> str:
    """Достаёт ссылку на объявление, записывает клик и уведомляет админов. Возвращает текст для клиента."""
    exchange = EXCHANGES.get(exchange_key)
    if exchange is None:
        return "Эта биржа сейчас недоступна."

    ad = exchange.get_my_ad(side=side, token=config.TOKEN, currency=config.CURRENCY)
    if not ad or not ad.get("link"):
        return "Ссылка временно недоступна, попробуй позже."

    amount_line = f"\n💵 Сумма: <b>{amount}</b>" if amount else ""
    client_text = (
        f"✅ <b>{SIDE_LABELS[side]} USDT/RUB на {exchange.name}</b>"
        + (f"\n💱 Цена: <code>{ad['price']:.2f} ₽</code>" if ad.get("price") else "")
        + amount_line
        + f"\n\n👇 Нажми кнопку ниже, чтобы открыть сделку"
    )

    storage.record_click(exchange_key, side, username)

    notify_text = (
        f"👉 Клиент {username} выбрал: {SIDE_LABELS[side]} USDT/RUB на {exchange.name}"
        + (f" (~{ad['price']:.2f} ₽)" if ad.get("price") else "")
        + amount_line
    )
    for admin_id in config.ADMIN_CHAT_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=notify_text)
        except Exception as e:
            logger.error(f"Не удалось уведомить админа {admin_id}: {e}")

    return client_text, ad["link"]


async def ask_for_amount(query, exchange_key: str, side: str):
    chat_id = query.message.chat_id
    pending_amount_request[chat_id] = {"exchange": exchange_key, "side": side}
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("Пропустить", callback_data=f"amount_skip:{exchange_key}:{side}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"back_to_exchanges:{side}")],
    ])
    await query.edit_message_text(
        f"Какую сумму хочешь {SIDE_LABELS[side].lower()}? Напиши число (например 10000 ₽ или 100 USDT), "
        f"или нажми Пропустить.",
        reply_markup=buttons,
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("side:"):
        _, side = data.split(":", 1)
        await handle_side_choice(query, side)
    elif data.startswith("go:"):
        _, exchange_key, side = data.split(":", 2)
        await ask_for_amount(query, exchange_key, side)
    elif data == "back_to_main":
        pending_amount_request.pop(query.message.chat_id, None)
        await show_side_selection(query)
    elif data.startswith("back_to_exchanges:"):
        _, side = data.split(":", 1)
        pending_amount_request.pop(query.message.chat_id, None)
        await handle_side_choice(query, side)
    elif data.startswith("amount_skip:"):
        _, exchange_key, side = data.split(":", 2)
        pending_amount_request.pop(query.message.chat_id, None)
        user = query.from_user
        username = f"@{user.username}" if user.username else user.full_name
        text, link = await deliver_link(context, query.message.chat_id, username, exchange_key, side, amount=None)
        open_button = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"Открыть на {EXCHANGES[exchange_key].name} ↗", url=link)],
            [InlineKeyboardButton("⬅️ В начало", callback_data="back_to_main")],
        ])
        await query.edit_message_text(text, reply_markup=open_button)
    elif data == "broadcast_confirm":
        chat_id = query.message.chat_id
        text = pending_broadcast.pop(chat_id, None)
        if text is None:
            await query.edit_message_text("Рассылка устарела, попробуй снова.")
            return
        client_ids = storage.get_all_client_ids()
        sent = 0
        for cid in client_ids:
            try:
                await context.bot.send_message(chat_id=cid, text=text)
                sent += 1
            except Exception:
                pass
        await query.edit_message_text(f"Разослано {sent} из {len(client_ids)} пользователям.")
    elif data == "broadcast_cancel":
        pending_broadcast.pop(query.message.chat_id, None)
        await query.edit_message_text("Рассылка отменена.")
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
    elif data.startswith("setup_reset_price:"):
        _, exchange_key, side = data.split(":", 2)
        storage.delete_manual_price(exchange_key, side)
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
        [InlineKeyboardButton("🔄 Сбросить на авто (rufinex+5%)", callback_data=f"setup_reset_price:{exchange_key}:{side}")],
        [InlineKeyboardButton("🗑 Удалить объявление", callback_data=f"setup_delete_ask:{exchange_key}:{side}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"setup_back_side:{exchange_key}")],
    ]
    await query.edit_message_text(
        f"⚙️ <b>{name}</b> — {SIDE_LABELS[side]}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🔗 Ссылка: {link_text}\n"
        f"💰 Цена: <code>{price_text}</code>",
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
            InlineKeyboardButton("✅ Да, удалить", callback_data=f"setup_delete_yes:{exchange_key}:{side}", style="danger"),
            InlineKeyboardButton("❌ Отмена", callback_data=f"setup_side:{exchange_key}:{side}", style="primary"),
        ]
    ]
    await query.edit_message_text(
        f"Точно удалить объявление {name} ({SIDE_LABELS[side]})? Ссылка и цена будут стёрты.",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def handle_setup_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ловит текстовые ответы: либо сумму от клиента, либо ссылку/цену от админа в /setup."""
    chat_id = update.effective_chat.id

    if chat_id in pending_amount_request:
        amount_state = pending_amount_request.pop(chat_id)
        user = update.effective_user
        username = f"@{user.username}" if user.username else user.full_name
        text, link = await deliver_link(
            context, chat_id, username,
            amount_state["exchange"], amount_state["side"],
            amount=update.message.text.strip(),
        )
        open_button = InlineKeyboardMarkup([
            InlineKeyboardButton(f"Открыть на {EXCHANGES[exchange_key].name} ↗", url=link, style="success")
            [InlineKeyboardButton("⬅️ В начало", callback_data="back_to_main")],
        ])
        await update.message.reply_text(text, reply_markup=open_button)
        return

    state = pending_setup.get(chat_id)
    if state is None:
        return  # ни мастер, ни ожидание суммы не активны - ничего не делаем

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
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    rows = storage.get_click_stats()
    if not rows:
        await update.message.reply_text("Пока нет статистики кликов.")
        return

    lines = ["📈 <b>Статистика кликов клиентов</b>", "━━━━━━━━━━━━━━━"]
    for row in rows:
        exchange_name = EXCHANGES[row["exchange"]].name if row["exchange"] in EXCHANGES else row["exchange"]
        lines.append(f"<b>{exchange_name}</b> — {SIDE_LABELS[row['side']]}: <code>{row['count']}</code>")

    await update.message.reply_text("\n".join(lines))
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not context.args:
        await update.message.reply_text("Формат: /broadcast текст сообщения")
        return

    text = " ".join(context.args)
    client_ids = storage.get_all_client_ids()
    pending_broadcast[update.effective_chat.id] = text

    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Отправить", callback_data="broadcast_confirm"),
        InlineKeyboardButton("❌ Отмена", callback_data="broadcast_cancel"),
    ]])
    await update.message.reply_text(
        f"Разослать это сообщение {len(client_ids)} пользователям?\n\n{text}",
        reply_markup=buttons,
    )


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
            for admin_id in config.ADMIN_CHAT_IDS:
                await context.bot.send_message(chat_id=admin_id, text=text)

# ---------------------------------------------------------------------------
# ЗАПУСК
# ---------------------------------------------------------------------------

def main():
    if not config.TELEGRAM_BOT_TOKEN or not config.ADMIN_CHAT_IDS:
        print("ОШИБКА: заполни TELEGRAM_BOT_TOKEN и ADMIN_CHAT_IDS в .env")
        return

    storage.init_db()

    defaults = Defaults(parse_mode="HTML")
    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).defaults(defaults).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CommandHandler("setup", setup))
    application.add_handler(CommandHandler("setlink", setlink))
    application.add_handler(CommandHandler("setprice", setprice))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.Text(["▶️ Старт", "🔄 Рестарт"]), show_main_menu))
    application.add_handler(MessageHandler(filters.Text(["⚙️ Настройка"]), setup))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.Text(["❓ Помощь"]), help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_setup_text))

    application.job_queue.run_repeating(check_new_orders, interval=config.CHECK_ORDERS_INTERVAL, first=10)

    print("Мультибиржевый P2P-бот запущен! Ctrl+C для остановки.")
    application.run_polling()


if __name__ == "__main__":
    main()
