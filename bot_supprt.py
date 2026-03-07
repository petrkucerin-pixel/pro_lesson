"""
🏫 ПроУрок — Бот поддержки и продаж
Гибридный бот: ИИ (Claude) + живая поддержка
Тарифы, оплата, FAQ, связь с администратором
"""

import os
import logging
from datetime import datetime
from anthropic import Anthropic

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters, ConversationHandler
)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
client = Anthropic(api_key=ANTHROPIC_API_KEY)

# ─── Админы ───────────────────────────────────────────────────
ADMIN_IDS = [1306327841, 5185799596]  # Пётр, Алевтина
ADMIN_NAMES = {1306327841: "Пётр", 5185799596: "Алевтина"}

# ─── Ссылки ───────────────────────────────────────────────────
PROUROK_BOT_URL = "https://t.me/pro_lesson_bot"
CARD_NUMBER = "4081-7810-6096-6003-4765"  # TODO: заменить на реальный номер карты
CARD_HOLDER = "Имя Фамилия"  # TODO: заменить

# ─── Тарифы (заглушки — заменить на реальные) ─────────────────
TARIFFS = {
    "start": {
        "name": "🟢 Старт",
        "price": "XXX ₽/мес",
        "desc": "• До 10 запросов в день\n• Текстовые ответы\n• 5 документов в день\n• Поддержка в чате",
    },
    "pro": {
        "name": "🔵 Про",
        "price": "XXX ₽/мес",
        "desc": "• До 30 запросов в день\n• Все типы документов\n• 15 документов в день\n• Приоритетная поддержка",
    },
    "premium": {
        "name": "🟡 Премиум",
        "price": "XXX ₽/мес",
        "desc": "• Безлимитные запросы\n• Безлимитные документы\n• Персональная настройка\n• Поддержка 24/7",
    },
}

# ─── Режимы пользователей ─────────────────────────────────────
# "ai" — отвечает Claude
# "human" — переключён на живого админа
# "payment" — процесс оплаты
user_modes = {}
user_conversations = {}  # история для Claude
user_selected_tariff = {}  # выбранный тариф
MAX_HISTORY = 16

# ─── Системный промпт для ИИ-поддержки ───────────────────────
SUPPORT_SYSTEM_PROMPT = """Ты — бот поддержки сервиса «ПроУрок». Твоя задача — помогать учителям разобраться с ботом и отвечать на вопросы.

О СЕРВИСЕ «ПРОУРОК»:
ПроУрок — это Telegram-бот ИИ-помощник для учителей любого предмета.
Бот доступен по ссылке: """ + PROUROK_BOT_URL + """

ЧТО УМЕЕТ ПРОУРОК:
- Создание планов уроков (1-11 класс, любой предмет)
- Генерация тестов, контрольных работ, викторин с ответами
- Подготовка конспектов и методических материалов
- Создание рабочих листов и дидактических карточек
- Помощь с рабочими программами по ФГОС
- Подготовка к ОГЭ/ЕГЭ по любому предмету
- Создание презентаций PowerPoint с диаграммами и картинками
- Создание документов Word и таблиц Excel
- Анализ фотографий учебников, тетрадей, заданий
- Распознавание голосовых сообщений
- Чтение документов (Word, PDF, Excel)

КАК ПОЛЬЗОВАТЬСЯ:
1. Откройте бот @pro_lesson_bot
2. Нажмите /start
3. Напишите что вам нужно — бот ответит текстом
4. Обсуждайте, уточняйте, дополняйте
5. Когда материал готов — нажмите кнопку «Оформить презентацию», «Оформить Word» или «Оформить Excel»
6. Бот создаст файл и отправит вам

ВАЖНО:
- Бот понимает текст, голос, фото и документы
- Можно отправить фото учебника и попросить составить тест
- Можно отправить голосовое сообщение с запросом
- Бот помнит контекст диалога — можно уточнять и дополнять

ДЕМО-ДОСТУП:
- 20 текстовых запросов
- 5 оформлений документов (PPTX, Word, Excel)
- После исчерпания — предлагается подключить тариф

ТАРИФЫ:
Тарифы находятся в разработке. Если учитель спрашивает о ценах — предложи нажать кнопку «Тарифы» в меню или написать администратору.

ПРАВИЛА ОБЩЕНИЯ:
- Отвечай на русском языке
- Будь вежлив и терпелив — учителя могут быть не знакомы с технологиями
- Отвечай кратко и по делу
- Если не знаешь ответа — предложи связаться с администратором
- НЕ выдумывай функции которых нет
- НЕ называй конкретные цены — направляй к тарифам
- Если вопрос про оплату, возврат, техническую проблему — предложи связаться с человеком
"""


def get_mode(user_id):
    return user_modes.get(user_id, "ai")

def set_mode(user_id, mode):
    user_modes[user_id] = mode

def get_ai_history(user_id):
    if user_id not in user_conversations:
        user_conversations[user_id] = []
    return user_conversations[user_id]

def add_ai_history(user_id, role, content):
    history = get_ai_history(user_id)
    history.append({"role": role, "content": content})
    if len(history) > MAX_HISTORY:
        user_conversations[user_id] = history[-MAX_HISTORY:]

def ask_support_claude(user_id, message):
    """Запрос к Claude для ИИ-поддержки."""
    add_ai_history(user_id, "user", message)
    history = get_ai_history(user_id)
    try:
        response = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=1024,
            system=SUPPORT_SYSTEM_PROMPT,
            messages=history
        )
        answer = response.content[0].text
        add_ai_history(user_id, "assistant", answer)
        return answer
    except Exception as e:
        logger.error(f"Claude: {e}")
        return "⚠️ Произошла ошибка. Попробуйте позже или свяжитесь с администратором."


def get_user_info(update):
    """Собирает инфо о пользователе для админов."""
    user = update.effective_user
    parts = []
    if user.first_name: parts.append(user.first_name)
    if user.last_name: parts.append(user.last_name)
    name = ' '.join(parts) or "Неизвестно"
    username = f"@{user.username}" if user.username else "нет username"
    return name, username, user.id


async def notify_admins(context, text):
    """Отправляет сообщение всем админам."""
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=text, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Не удалось отправить админу {admin_id}: {e}")


# ─── Обработчики ──────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    set_mode(user_id, "ai")

    keyboard = [
        [InlineKeyboardButton("📋 Тарифы", callback_data="tariffs"),
         InlineKeyboardButton("💬 Задать вопрос", callback_data="ask_question")],
        [InlineKeyboardButton("📖 Как пользоваться", callback_data="how_to"),
         InlineKeyboardButton("👨‍💻 Связаться с человеком", callback_data="connect_human")],
        [InlineKeyboardButton("🚀 Открыть ПроУрок", url=PROUROK_BOT_URL)],
    ]

    welcome = (
        "🏫 **Добро пожаловать в поддержку «ПроУрок»!**\n\n"
        "Я помогу вам разобраться с ИИ-помощником для учителей.\n\n"
        "**Что я могу:**\n"
        "💬 Ответить на вопросы о боте\n"
        "📋 Показать тарифы и помочь подключить\n"
        "📖 Объяснить как пользоваться\n"
        "👨‍💻 Переключить на живого специалиста\n\n"
        "Выберите действие или просто напишите вопрос:"
    )
    await update.message.reply_text(welcome, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    # ─── ТАРИФЫ ───
    if data == "tariffs":
        text = "📋 **Тарифы «ПроУрок»:**\n\n"
        for key, tariff in TARIFFS.items():
            text += f"**{tariff['name']}** — {tariff['price']}\n{tariff['desc']}\n\n"
        text += "Выберите тариф для подключения:"

        keyboard = [
            [InlineKeyboardButton("🟢 Старт", callback_data="buy_start"),
             InlineKeyboardButton("🔵 Про", callback_data="buy_pro")],
            [InlineKeyboardButton("🟡 Премиум", callback_data="buy_premium")],
            [InlineKeyboardButton("◀️ Назад", callback_data="back_main")],
        ]
        await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    # ─── ВЫБОР ТАРИФА → ОПЛАТА ───
    elif data.startswith("buy_"):
        tariff_key = data[4:]  # start, pro, premium
        tariff = TARIFFS.get(tariff_key)
        if not tariff:
            await query.message.reply_text("⚠️ Тариф не найден."); return

        user_selected_tariff[user_id] = tariff_key

        text = (
            f"✅ Вы выбрали тариф **{tariff['name']}** — {tariff['price']}\n\n"
            f"**Для оплаты переведите на карту:**\n"
            f"`{CARD_NUMBER}`\n"
            f"Получатель: {CARD_HOLDER}\n"
            f"Сумма: **{tariff['price']}**\n\n"
            "После перевода нажмите кнопку ниже:"
        )
        keyboard = [
            [InlineKeyboardButton("✅ Я оплатил(а)", callback_data="paid")],
            [InlineKeyboardButton("◀️ Назад к тарифам", callback_data="tariffs")],
        ]
        await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    # ─── ПОДТВЕРЖДЕНИЕ ОПЛАТЫ ───
    elif data == "paid":
        name, username, uid = get_user_info(query)
        tariff_key = user_selected_tariff.get(user_id, "неизвестен")
        tariff = TARIFFS.get(tariff_key, {})
        tariff_name = tariff.get('name', tariff_key)

        # Уведомляем админов
        admin_msg = (
            f"💰 **НОВАЯ ОПЛАТА!**\n\n"
            f"👤 {name} ({username})\n"
            f"🆔 `{uid}`\n"
            f"📋 Тариф: {tariff_name}\n"
            f"🕐 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
            f"Проверьте поступление и подтвердите доступ."
        )
        await notify_admins(context, admin_msg)

        await query.message.reply_text(
            "🎉 **Спасибо! Заявка отправлена.**\n\n"
            "Мы проверим оплату и откроем доступ в течение нескольких часов.\n"
            "Вам придёт уведомление в этом чате.\n\n"
            "Если есть вопросы — просто напишите сюда.",
            parse_mode='Markdown'
        )

    # ─── КАК ПОЛЬЗОВАТЬСЯ ───
    elif data == "how_to":
        text = (
            "📖 **Как пользоваться «ПроУрок»:**\n\n"
            f"1️⃣ Откройте бот: {PROUROK_BOT_URL}\n"
            "2️⃣ Нажмите /start\n"
            "3️⃣ Напишите что вам нужно, например:\n"
            "   • «Тест по математике, 5 класс, дроби»\n"
            "   • «План урока: Фотосинтез, биология»\n"
            "   • «Разбор задания ЕГЭ по русскому»\n"
            "4️⃣ Бот ответит текстом — обсуждайте, уточняйте\n"
            "5️⃣ Нажмите кнопку оформления внизу:\n"
            "   📽 Презентация • 📄 Word • 📊 Excel\n\n"
            "**Также можно:**\n"
            "📸 Отправить фото учебника\n"
            "🎙 Записать голосовое сообщение\n"
            "📄 Отправить документ (Word, PDF)\n\n"
            "Есть вопросы? Просто напишите!"
        )
        keyboard = [
            [InlineKeyboardButton("🚀 Открыть ПроУрок", url=PROUROK_BOT_URL)],
            [InlineKeyboardButton("◀️ Назад", callback_data="back_main")],
        ]
        await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    # ─── ЗАДАТЬ ВОПРОС (ИИ) ───
    elif data == "ask_question":
        set_mode(user_id, "ai")
        await query.message.reply_text(
            "💬 Задайте ваш вопрос — я постараюсь помочь!\n\n"
            "Если потребуется живой специалист — напишите «позови человека»."
        )

    # ─── СВЯЗАТЬСЯ С ЧЕЛОВЕКОМ ───
    elif data == "connect_human":
        set_mode(user_id, "human")
        name, username, uid = get_user_info(query)

        admin_msg = (
            f"📩 **Запрос на связь с человеком**\n\n"
            f"👤 {name} ({username})\n"
            f"🆔 `{uid}`\n"
            f"🕐 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
            f"Ответьте командой:\n`/reply {uid} ваш текст`"
        )
        await notify_admins(context, admin_msg)

        await query.message.reply_text(
            "👨‍💻 **Переключаю на специалиста.**\n\n"
            "Напишите ваш вопрос — мы ответим как можно скорее.\n"
            "Обычно отвечаем в течение 15-30 минут.\n\n"
            "Чтобы вернуться к ИИ-помощнику — напишите /start",
            parse_mode='Markdown'
        )

    # ─── НАЗАД ───
    elif data == "back_main":
        keyboard = [
            [InlineKeyboardButton("📋 Тарифы", callback_data="tariffs"),
             InlineKeyboardButton("💬 Задать вопрос", callback_data="ask_question")],
            [InlineKeyboardButton("📖 Как пользоваться", callback_data="how_to"),
             InlineKeyboardButton("👨‍💻 Связаться с человеком", callback_data="connect_human")],
            [InlineKeyboardButton("🚀 Открыть ПроУрок", url=PROUROK_BOT_URL)],
        ]
        await query.message.reply_text("🏫 Выберите действие:", reply_markup=InlineKeyboardMarkup(keyboard))


# ─── Команда ответа для админов ───────────────────────────────

async def reply_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ отвечает пользователю: /reply USER_ID текст"""
    admin_id = update.effective_user.id
    if admin_id not in ADMIN_IDS:
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Формат: `/reply USER_ID текст ответа`", parse_mode='Markdown')
        return

    try:
        target_uid = int(context.args[0])
        reply_text = ' '.join(context.args[1:])
    except ValueError:
        await update.message.reply_text("⚠️ Неверный ID пользователя."); return

    admin_name = ADMIN_NAMES.get(admin_id, "Специалист")
    try:
        await context.bot.send_message(
            chat_id=target_uid,
            text=f"👨‍💻 **{admin_name}:**\n\n{reply_text}",
            parse_mode='Markdown'
        )
        await update.message.reply_text(f"✅ Ответ отправлен пользователю {target_uid}.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Ошибка отправки: {e}")


# ─── Команда подтверждения доступа ────────────────────────────

async def confirm_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ подтверждает оплату: /confirm USER_ID"""
    admin_id = update.effective_user.id
    if admin_id not in ADMIN_IDS:
        return

    if not context.args:
        await update.message.reply_text("Формат: `/confirm USER_ID`", parse_mode='Markdown'); return

    try:
        target_uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ Неверный ID."); return

    tariff_key = user_selected_tariff.get(target_uid, "")
    tariff = TARIFFS.get(tariff_key, {})
    tariff_name = tariff.get('name', 'Тариф')

    try:
        await context.bot.send_message(
            chat_id=target_uid,
            text=(
                f"🎉 **Доступ активирован!**\n\n"
                f"Тариф: {tariff_name}\n"
                f"Дата начала: {datetime.now().strftime('%d.%m.%Y')}\n\n"
                f"Откройте бот и пользуйтесь без ограничений:\n"
                f"👉 {PROUROK_BOT_URL}\n\n"
                f"Спасибо что выбрали «ПроУрок»! 🏫"
            ),
            parse_mode='Markdown'
        )
        await update.message.reply_text(f"✅ Доступ подтверждён для {target_uid}.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Ошибка: {e}")


# ─── Обработка сообщений ─────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    # Админ пишет в бот — игнорируем (он использует /reply)
    if user_id in ADMIN_IDS:
        await update.message.reply_text(
            "👤 Вы администратор.\n\n"
            "Команды:\n"
            "`/reply USER_ID текст` — ответить пользователю\n"
            "`/confirm USER_ID` — подтвердить оплату\n"
            "`/stats` — статистика",
            parse_mode='Markdown'
        )
        return

    mode = get_mode(user_id)

    # Проверяем запросы на переключение
    text_lower = text.lower()
    if any(kw in text_lower for kw in ['позови человека', 'живой человек', 'оператор', 'менеджер', 'администратор']):
        set_mode(user_id, "human")
        name, username, uid = get_user_info(update)
        admin_msg = (
            f"📩 **Пользователь просит человека**\n\n"
            f"👤 {name} ({username})\n"
            f"🆔 `{uid}`\n"
            f"💬 «{text}»\n\n"
            f"Ответ: `/reply {uid} текст`"
        )
        await notify_admins(context, admin_msg)
        await update.message.reply_text(
            "👨‍💻 Переключаю на специалиста. Напишите ваш вопрос — ответим как можно скорее!",
            parse_mode='Markdown'
        )
        return

    # РЕЖИМ: живой человек
    if mode == "human":
        name, username, uid = get_user_info(update)
        admin_msg = (
            f"💬 **Сообщение от пользователя:**\n\n"
            f"👤 {name} ({username})\n"
            f"🆔 `{uid}`\n\n"
            f"«{text}»\n\n"
            f"Ответ: `/reply {uid} текст`"
        )
        await notify_admins(context, admin_msg)
        await update.message.reply_text("✅ Сообщение передано специалисту. Ожидайте ответа.")
        return

    # РЕЖИМ: ИИ
    answer = ask_support_claude(user_id, text)

    keyboard = [
        [InlineKeyboardButton("📋 Тарифы", callback_data="tariffs"),
         InlineKeyboardButton("👨‍💻 Связаться с человеком", callback_data="connect_human")],
    ]
    await update.message.reply_text(answer, reply_markup=InlineKeyboardMarkup(keyboard))


# ─── Статистика для админов ───────────────────────────────────

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    total_users = len(user_modes)
    ai_users = sum(1 for m in user_modes.values() if m == "ai")
    human_users = sum(1 for m in user_modes.values() if m == "human")
    payments = len(user_selected_tariff)
    await update.message.reply_text(
        f"📊 **Статистика:**\n\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"🤖 В ИИ-режиме: {ai_users}\n"
        f"👨‍💻 Ждут человека: {human_users}\n"
        f"💰 Заявок на оплату: {payments}",
        parse_mode='Markdown'
    )


# ─── Ошибки ──────────────────────────────────────────────────

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Ошибка: {context.error}")


# ─── Запуск ──────────────────────────────────────────────────

def main():
    if not TELEGRAM_TOKEN or not ANTHROPIC_API_KEY:
        print("❌ Нет токенов!"); return
    print("🏫 ПроУрок — Поддержка запускается...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reply", reply_command))
    app.add_handler(CommandHandler("confirm", confirm_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)
    print("✅ Бот поддержки запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
