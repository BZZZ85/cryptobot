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
    /setlimit <buy|sell> <сумма>           - задать доступный объём (0 - убрать лимит)
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
import rufinex_client

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

EXCHANGES = config.build_exchanges()

SIDE_LABELS = {"buy": "Купить", "sell": "Продать"}
EXCHANGE_EMOJI = {
    "bybit": "",
    "bitget": "",
    "TELEGRAMWALLET": "",
    "mexc": "",
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


def build_side_buttons() -> InlineKeyboardMarkup:
    buy_limit = rufinex_client.get_available_limit("buy")
    sell_limit = rufinex_client.get_available_limit("sell")
    buy_text = "💵 Купить USDT" + (f" (до {rufinex_client.format_limit(buy_limit)})" if buy_limit else "")
    sell_text = "💰 Продать USDT" + (f" (до {rufinex_client.format_limit(sell_limit)})" if sell_limit else "")
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(buy_text, callback_data="side:buy", style="success"),
            InlineKeyboardButton(sell_text, callback_data="side:sell", style="danger"),
        ]
    ])


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает курсы всех бирж + кнопки Купить/Продать. Вызывается из /start и кнопок Старт/Рестарт."""
    pending_setup.pop(update.effective_chat.id, None)  # сбрасываем зависшие состояния мастера, если были
    user = update.effective_user
    username = f"@{user.username}" if user.username else user.full_name

    referred_by = None
    if context.args:
        arg = context.args[0]
        if arg.startswith("ref_"):
            try:
                candidate = int(arg[4:])
                if candidate != update.effective_chat.id:
                    referred_by = candidate
            except ValueError:
                pass

    is_new_client = storage.record_client(update.effective_chat.id, username, referred_by=referred_by)

    if is_new_client and config.WELCOME_STICKER_ID:
        try:
            await update.message.reply_sticker(config.WELCOME_STICKER_ID)
        except Exception as e:
            logger.error(f"Не удалось отправить приветственный стикер: {e}")
    

    rates_text = await build_rates_text()

    inline_buttons = build_side_buttons()

    # Два сообщения: одно держит постоянную клавиатуру снизу, второе - курсы и инлайн-кнопки
    keyboard = ADMIN_KEYBOARD if is_admin(update) else MAIN_KEYBOARD
    if os.path.exists(BANNER_PATH):
        with open(BANNER_PATH, "rb") as photo:
            await update.message.reply_photo(
                photo=photo,
                caption=(
                    "🤖 <b>USDT/RUB Обменник ExUma</b>\n"
                    "Быстро и по актуальному курсу\n\n"
                    "<b>Как это работает:</b>\n"
                    "1️⃣ Жми «Купить» или «Продать» ниже\n"
                    "2️⃣ Выбери подходящую биржу\n"
                    "3️⃣ Укажи сумму (или пропусти этот шаг)\n"
                    "4️⃣ Перейди по ссылке и выбери нужное объявление из списка, заверши сделку прямо на бирже\n\n"
                    "❓ Вопросы — команда /help"
                ),
            )

    await update.message.reply_text("👇 Что делаем дальше?", reply_markup=keyboard)
    await update.message.reply_text(rates_text, reply_markup=inline_buttons)

# Состояние пошагового мастера /setup для админа:
# pending_setup[chat_id] = {"exchange": "htx", "side": "buy", "step": "link"|"price"}
pending_setup: dict[int, dict] = {}
pending_amount_request: dict[int, dict] = {}
pending_broadcast: dict[int, str] = {}
async def show_side_selection(query):
    inline_buttons = build_side_buttons()
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

    ad["price"] = rufinex_client.apply_loyalty_discount(ad.get("price"), side, chat_id)
    is_loyal = rufinex_client.is_loyal_client(chat_id)

    amount_line = f"\n💵 Сумма: <b>{amount}</b>" if amount else ""
    loyalty_line = "\n🎁 Применена скидка постоянного клиента" if is_loyal else ""
    limit_warning = rufinex_client.check_amount_limit(amount, side)
    limit_warning_line = f"\n{limit_warning}" if limit_warning else ""
    client_text = (
        f"✅ <b>{SIDE_LABELS[side]} USDT/RUB на {exchange.name}</b>"
        + (f"\n💱 Цена: <code>{ad['price']:.2f} ₽</code>" if ad.get("price") else "")
        + amount_line
        + loyalty_line
        + limit_warning_line
        + f"\n\n👇 Нажми кнопку ниже, чтобы открыть сделку"
    )

    storage.record_click(exchange_key, side, username, chat_id)

    profile = storage.get_client_profile(chat_id) or {}
    profile_line = ""
    if profile.get("full_name"):
        profile_line += f"\n👤 ФИО: {profile['full_name']}"
    if profile.get("exchange_nickname"):
        profile_line += f"\n🏷 Ник на бирже: {profile['exchange_nickname']}"

    notify_text = (
        f"👉 Клиент {username} выбрал: {SIDE_LABELS[side]} USDT/RUB на {exchange.name}"
        + (f" (~{ad['price']:.2f} ₽)" if ad.get("price") else "")
        + amount_line
        + profile_line
    )
    for admin_id in config.ADMIN_CHAT_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=notify_text)
        except Exception as e:
            logger.error(f"Не удалось уведомить админа {admin_id}: {e}")

    if context.job_queue:
        context.job_queue.run_once(
            ask_for_review,
            when=1200,  # через 20 минут - к этому моменту сделка обычно уже завершена
            data={"exchange": exchange_key, "side": side, "chat_id": chat_id},
        )

    return client_text, ad["link"]


async def ask_for_review(context: ContextTypes.DEFAULT_TYPE):
    """Отложенный запрос оценки сделки - ставится в очередь из deliver_link."""
    data = context.job.data
    exchange_name = EXCHANGES[data["exchange"]].name if data["exchange"] in EXCHANGES else data["exchange"]
    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton(str(n), callback_data=f"review:{data['exchange']}:{data['side']}:{n}")
        for n in range(1, 6)
    ]])
    try:
        await context.bot.send_message(
            chat_id=data["chat_id"],
            text=f"Как прошла сделка на {exchange_name}? Оцени от 1 до 5 ⭐",
            reply_markup=buttons,
        )
    except Exception as e:
        logger.error(f"Не удалось запросить отзыв у {data['chat_id']}: {e}")


async def ask_for_amount(query, exchange_key: str, side: str):
    chat_id = query.message.chat_id
    pending_amount_request[chat_id] = {"exchange": exchange_key, "side": side}
    limit = rufinex_client.get_available_limit(side)
    limit_line = f"\nДоступно: до {rufinex_client.format_limit(limit)}." if limit else ""
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("Пропустить", callback_data=f"amount_skip:{exchange_key}:{side}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"back_to_exchanges:{side}")],
    ])
    await query.edit_message_text(
        f"Какую сумму хочешь {SIDE_LABELS[side].lower()}? Напиши число (например 10000 ₽ или 100 USDT), "
        f"или нажми Пропустить." + limit_line,
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
    elif data.startswith("review:"):
        _, exchange_key, side, rating = data.split(":", 3)
        storage.record_review(query.message.chat_id, exchange_key, side, int(rating))
        await query.edit_message_text("Спасибо за оценку! 🙏")
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
            [InlineKeyboardButton(f"Открыть на {EXCHANGES[amount_state['exchange']].name} ↗", url=link, style="success")],
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
        await update.message.reply_text("Формат: /setlink <биржа> <buy|sell> <ссылка>\nПример: /setlink wallet buy https://...")
        return

    exchange_key, side, url = context.args[0].lower(), context.args[1].lower(), context.args[2]
    if exchange_key not in EXCHANGES or EXCHANGES[exchange_key].has_api:
        await update.message.reply_text(
            "Эта команда только для 'ручных' бирж (например wallet, mexc)."
        )
        return
    if side not in ("buy", "sell"):
        await update.message.reply_text("Сторона должна быть buy или sell.")
        return

    storage.set_manual_ad(exchange_key, side, url=url)
    await update.message.reply_text(f"Ссылка для {exchange_key} ({side}) сохранена.")


async def setlimit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if len(context.args) < 2:
        await update.message.reply_text(
            "Формат: /setlimit <buy|sell> <сумма_в_рублях>\n"
            "Пример: /setlimit buy 250000\n"
            "Чтобы убрать лимит: /setlimit buy 0"
        )
        return

    side = context.args[0].lower()
    if side not in ("buy", "sell"):
        await update.message.reply_text("Сторона должна быть buy или sell.")
        return
    try:
        amount = float(context.args[1])
    except ValueError:
        await update.message.reply_text("Сумма должна быть числом.")
        return

    if amount <= 0:
        rufinex_client.set_available_limit(side, None)
        await update.message.reply_text(f"Лимит для «{SIDE_LABELS[side]}» убран.")
    else:
        rufinex_client.set_available_limit(side, amount)
        await update.message.reply_text(f"Лимит для «{SIDE_LABELS[side]}» установлен: {rufinex_client.format_limit(amount)}")


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
        await update.message.reply_text("Эта команда только для 'ручных' бирж (например wallet, mexc).")
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
            storage.mark_order_seen(order["order_id"], exchange=key)

            text = (
                f"🔔 Новый ордер на {exchange.name}!\n"
                f"Ник: {order['nick']}\n"
                f"Сумма: {order['amount']} {order['currency']}\n"
                f"Количество: {order['quantity']} {order['token']}"
            )
            for admin_id in config.ADMIN_CHAT_IDS:
                await context.bot.send_message(chat_id=admin_id, text=text)
async def record_rate_history(context: ContextTypes.DEFAULT_TYPE):
    try:
        rates = rufinex_client.fetch_base_rates()
        if rates:
            storage.record_rate_snapshot(rates["buy"], rates["sell"])
    except Exception as e:
        logger.error(f"Не удалось сохранить историю курса: {e}")


async def setmarkup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not context.args:
        current = rufinex_client.get_markup_percent()
        await update.message.reply_text(f"Текущая наценка: {current}%.\nЧтобы изменить: /setmarkup 7.5")
        return
    try:
        value = float(context.args[0].replace(",", "."))
    except ValueError:
        await update.message.reply_text("Это не похоже на число. Пример: /setmarkup 7.5")
        return
    rufinex_client.set_markup_percent(value)
    await update.message.reply_text(f"✅ Наценка обновлена: {value}% (применится к ценам, которые считаются автоматически)")

async def setdiscount_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админская команда: /setdiscount @username 3  или  /setdiscount 123456789 3"""
    if not is_admin(update):
        return
    if len(context.args) < 2:
        await update.message.reply_text(
            "Формат: /setdiscount <@username или chat_id> <процент>\n"
            "Пример: /setdiscount @ivan_petrov 3\n"
            "Чтобы убрать скидку - укажи 0"
        )
        return

    target, percent_raw = context.args[0], context.args[1]

    if target.startswith("@"):
        chat_id = storage.find_chat_id_by_username(target)
        if chat_id is None:
            await update.message.reply_text(f"Не нашёл клиента {target} - он должен хотя бы раз написать боту (/start).")
            return
    else:
        try:
            chat_id = int(target)
        except ValueError:
            await update.message.reply_text("Укажи @username или числовой chat_id.")
            return

    try:
        percent = float(percent_raw.replace(",", "."))
    except ValueError:
        await update.message.reply_text("Процент должен быть числом, например: 3 или 2.5")
        return

    storage.set_manual_discount(chat_id, percent)
    if percent > 0:
        await update.message.reply_text(f"✅ Клиенту {target} (id {chat_id}) выдана скидка {percent}%")
    else:
        await update.message.reply_text(f"✅ Скидка у клиента {target} (id {chat_id}) убрана")
async def check_price_alerts(context: ContextTypes.DEFAULT_TYPE):
    """Раз в 5 минут сверяет базовый курс rufinex с алертами клиентов и уведомляет при срабатывании."""
    try:
        rates = rufinex_client.fetch_base_rates()
        if not rates:
            return
        for alert in storage.get_all_active_alerts():
            price = rates["buy"] if alert["side"] == "buy" else rates["sell"]
            triggered = (
                (alert["direction"] == "above" and price >= alert["threshold"])
                or (alert["direction"] == "below" and price <= alert["threshold"])
            )
            if not triggered:
                continue
            direction_text = "выше" if alert["direction"] == "above" else "ниже"
            try:
                await context.bot.send_message(
                    chat_id=alert["chat_id"],
                    text=(
                        f"🔔 Курс {SIDE_LABELS[alert['side']].lower()} USDT/RUB {direction_text} "
                        f"{alert['threshold']:.2f} ₽ — сейчас {price:.2f} ₽!"
                    ),
                )
            except Exception as e:
                logger.error(f"Не удалось уведомить об алерте {alert['chat_id']}: {e}")
            storage.deactivate_alert(alert["id"])
    except Exception as e:
        logger.error(f"Ошибка проверки ценовых алертов: {e}")
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    had_state = pending_amount_request.pop(chat_id, None) or pending_setup.pop(chat_id, None)
    if had_state:
        await update.message.reply_text("Отменено. Можешь начать заново — /start или ⚙️ Настройка.")
    else:
        await update.message.reply_text("Сейчас нечего отменять.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Ловит все необработанные исключения в хендлерах и репортит админам, чтобы не летать в потёмках."""
    logger.error("Необработанная ошибка", exc_info=context.error)
    for admin_id in config.ADMIN_CHAT_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"⚠️ Ошибка в боте:\n<code>{context.error}</code>",
            )
        except Exception:
            pass


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
    application.add_handler(CommandHandler("setlimit", setlimit))
    application.add_handler(CommandHandler("setmarkup", setmarkup))
    application.add_handler(CommandHandler("setdiscount", setdiscount_command))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.Text(["▶️ Старт", "🔄 Рестарт"]), show_main_menu))
    application.add_handler(MessageHandler(filters.Text(["⚙️ Настройка"]), setup))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.Text(["❓ Помощь"]), help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_setup_text))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_error_handler(error_handler)

    application.job_queue.run_repeating(check_new_orders, interval=config.CHECK_ORDERS_INTERVAL, first=10)
    application.job_queue.run_repeating(record_rate_history, interval=300, first=15)
    application.job_queue.run_repeating(check_price_alerts, interval=300, first=20)

    print("Мультибиржевый P2P-бот запущен! Ctrl+C для остановки.")
    application.run_polling()


if __name__ == "__main__":
    main()
