"""
🏫 ПроУрок — Бот поддержки v2.0
Гибридный: ИИ (Claude) + живая поддержка
Админ-панель для Петра и Алевтины
Мониторинг API, управление пользователями, тарифы
"""

import os
import json
import logging
from datetime import datetime
from pathlib import Path
from anthropic import Anthropic

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
client = Anthropic(api_key=ANTHROPIC_API_KEY)

# ─── Админы ───────────────────────────────────────────────────
ADMIN_IDS = [1306327841, 5185799596]
ADMIN_NAMES = {1306327841: "Пётр", 5185799596: "Алевтина"}

# ─── Ссылки и реквизиты ──────────────────────────────────────
PROUROK_BOT_URL = "https://t.me/pro_lesson_bot"
CARD_NUMBER = "4081-7810-6096-6003-4765"  # TODO: заменить
CARD_HOLDER = "Имя Фамилия"  # TODO: заменить

# ─── Тарифы ───────────────────────────────────────────────────
TARIFFS = {
    "demo":    {"name": "🆓 Демо",    "price": "Бесплатно",   "queries": 20,  "gens": 5,   "price_num": 0},
    "start":   {"name": "🟢 Старт",   "price": "490₽/мес",    "queries": 200, "gens": 30,  "price_num": 490},
    "pro":     {"name": "🔵 Про",      "price": "890₽/мес",    "queries": 500, "gens": 100, "price_num": 890},
    "premium": {"name": "🟡 Премиум",  "price": "1 490₽/мес",  "queries": 999999, "gens": 999999, "price_num": 1490},
}

# Доп.пакет
ADDON_PRICE = "100₽"
ADDON_QUERIES = 50
ADDON_GENS = 10

# ─── JSON-хранилище (общее с ПроУрок) ────────────────────────
PROUROK_LIMITS_FILE = "/root/pro-lesson-bot/user_limits.json"

def load_prourok_data():
    if os.path.exists(PROUROK_LIMITS_FILE):
        try:
            with open(PROUROK_LIMITS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: return {}
    return {}

def save_prourok_data(data):
    try:
        with open(PROUROK_LIMITS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")

def set_user_tariff(user_id, tariff_key):
    """Устанавливает тариф пользователю и сбрасывает счётчики."""
    data = load_prourok_data()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {"tariff": "demo", "queries_used": 0, "generations_used": 0,
                     "registered": datetime.now().strftime("%d.%m.%Y"), "name": "", "username": ""}
    data[uid]["tariff"] = tariff_key
    data[uid]["queries_used"] = 0
    data[uid]["generations_used"] = 0
    data[uid]["tariff_date"] = datetime.now().strftime("%d.%m.%Y")
    save_prourok_data(data)

def add_addon(user_id):
    """Добавляет доп.пакет запросов."""
    data = load_prourok_data()
    uid = str(user_id)
    if uid in data:
        tariff = TARIFFS.get(data[uid].get("tariff", "demo"), TARIFFS["demo"])
        # Уменьшаем использованное (эффект = добавление запросов)
        data[uid]["queries_used"] = max(0, data[uid].get("queries_used", 0) - ADDON_QUERIES)
        data[uid]["generations_used"] = max(0, data[uid].get("generations_used", 0) - ADDON_GENS)
        save_prourok_data(data)
        return True
    return False

# ─── Режимы пользователей ─────────────────────────────────────
user_modes = {}  # "ai" или "human"
user_ai_history = {}
user_selected_tariff = {}
MAX_HISTORY = 16

def get_mode(uid): return user_modes.get(uid, "ai")
def set_mode(uid, mode): user_modes[uid] = mode

# ─── ИИ-поддержка ────────────────────────────────────────────

SUPPORT_PROMPT = f"""Ты — бот поддержки сервиса «ПроУрок». Помогаешь учителям.

О СЕРВИСЕ:
ПроУрок (@pro_lesson_bot) — ИИ-помощник для учителей любого предмета.

ВОЗМОЖНОСТИ БОТА:
- Планы уроков (1-11 класс, любой предмет)
- Тесты, контрольные, викторины с ответами
- Конспекты, рабочие листы, дидактические карточки
- Помощь с ФГОС, рабочие программы
- Подготовка к ОГЭ/ЕГЭ
- Презентации PowerPoint с диаграммами
- Документы Word, таблицы Excel
- Анализ фото учебников и заданий
- Голосовые запросы
- Чтение документов (Word, PDF, Excel)

КАК ПОЛЬЗОВАТЬСЯ:
1. Открыть @pro_lesson_bot → /start
2. Написать запрос текстом (или фото/голос)
3. Обсудить, уточнить
4. Нажать кнопку оформления (Презентация/Word/Excel)
5. Бот создаст файл

ТАРИФЫ:
🆓 Демо — бесплатно (20 запросов + 5 оформлений)
🟢 Старт — 490₽/мес (200 запросов + 30 оформлений)
🔵 Про — 890₽/мес (500 запросов + 100 оформлений)
🟡 Премиум — 1 490₽/мес (безлимит)
Доп.пакет: 100₽ за 50 запросов + 10 оформлений

ПРАВИЛА:
- Русский язык, вежливо, терпеливо
- Учителя могут быть не знакомы с технологиями — объясняй просто
- Если не знаешь — предложи связаться с человеком
- Вопросы про оплату, возврат, баги — предложи человека
- НЕ выдумывай функции
"""

def ask_support(uid, msg):
    if uid not in user_ai_history: user_ai_history[uid] = []
    user_ai_history[uid].append({"role": "user", "content": msg})
    if len(user_ai_history[uid]) > MAX_HISTORY:
        user_ai_history[uid] = user_ai_history[uid][-MAX_HISTORY:]
    try:
        r = client.messages.create(model="claude-3-haiku-20240307", max_tokens=1024,
                                    system=SUPPORT_PROMPT, messages=user_ai_history[uid])
        answer = r.content[0].text
        user_ai_history[uid].append({"role": "assistant", "content": answer})
        return answer
    except Exception as e:
        logger.error(f"Claude: {e}")
        return "⚠️ Ошибка. Попробуйте позже или напишите «позови человека»."

def get_user_info(update):
    u = update.effective_user
    name = f"{u.first_name or ''} {u.last_name or ''}".strip() or "Неизвестно"
    uname = f"@{u.username}" if u.username else "нет"
    return name, uname, u.id

async def notify_admins(context, text, keyboard=None):
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=aid, text=text, parse_mode='Markdown',
                                           reply_markup=keyboard)
        except Exception as e:
            logger.error(f"Админ {aid}: {e}")


# ─── АДМИН-ПАНЕЛЬ ────────────────────────────────────────────

async def admin_start(update, context):
    """Стартовое меню для админов."""
    name = ADMIN_NAMES.get(update.effective_user.id, "Админ")
    kb = [
        [InlineKeyboardButton("📊 Статистика", callback_data="adm_stats"),
         InlineKeyboardButton("👥 Пользователи", callback_data="adm_users")],
        [InlineKeyboardButton("💰 Баланс API", callback_data="adm_api_balance"),
         InlineKeyboardButton("📋 Заявки", callback_data="adm_orders")],
        [InlineKeyboardButton("📖 Команды", callback_data="adm_help")],
    ]
    await update.message.reply_text(
        f"👋 Привет, {name}!\n\n"
        f"🏫 **Админ-панель «ПроУрок»**\n\n"
        f"Выберите действие или используйте команды:",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
    )


async def admin_callback(update, context, data):
    """Обработка админских кнопок."""
    query = update.callback_query

    if data == "adm_stats":
        prourok = load_prourok_data()
        total = len(prourok)
        tariff_counts = {}
        total_queries = 0
        total_gens = 0
        for uid, udata in prourok.items():
            t = udata.get("tariff", "demo")
            tariff_counts[t] = tariff_counts.get(t, 0) + 1
            total_queries += udata.get("queries_used", 0)
            total_gens += udata.get("generations_used", 0)

        text = f"📊 **Статистика ПроУрок:**\n\n"
        text += f"👥 Всего пользователей: {total}\n\n"
        text += "**По тарифам:**\n"
        for tkey, tdata in TARIFFS.items():
            cnt = tariff_counts.get(tkey, 0)
            text += f"  {tdata['name']}: {cnt}\n"
        text += f"\n**Общий расход:**\n"
        text += f"💬 Запросов: {total_queries}\n"
        text += f"📄 Оформлений: {total_gens}\n"

        # Поддержка
        text += f"\n**Бот поддержки:**\n"
        text += f"💬 Активных диалогов: {len(user_modes)}\n"
        text += f"👨‍💻 Ожидают человека: {sum(1 for m in user_modes.values() if m == 'human')}\n"

        await query.message.reply_text(text, parse_mode='Markdown')

    elif data == "adm_users":
        prourok = load_prourok_data()
        if not prourok:
            await query.message.reply_text("👥 Пользователей пока нет."); return

        text = "👥 **Пользователи:**\n\n"
        for uid, udata in sorted(prourok.items(), key=lambda x: x[1].get("queries_used", 0), reverse=True)[:20]:
            name = udata.get("name", "?")
            uname = udata.get("username", "")
            tariff = udata.get("tariff", "demo")
            q = udata.get("queries_used", 0)
            g = udata.get("generations_used", 0)
            tname = TARIFFS.get(tariff, {}).get("name", tariff)
            text += f"• {name} ({uname}) — {tname}\n"
            text += f"  ID: `{uid}` | 💬{q} 📄{g}\n"

        if len(prourok) > 20:
            text += f"\n... и ещё {len(prourok) - 20}"

        await query.message.reply_text(text, parse_mode='Markdown')

    elif data == "adm_api_balance":
        # Проверяем баланс через API (если доступно)
        text = "💰 **Баланс API Anthropic:**\n\n"
        text += f"🔑 Ключ: ...{ANTHROPIC_API_KEY[-8:]}\n\n"

        # Считаем примерный расход
        prourok = load_prourok_data()
        total_q = sum(d.get("queries_used", 0) for d in prourok.values())
        total_g = sum(d.get("generations_used", 0) for d in prourok.values())
        # Примерная оценка: 3K токенов на запрос, 6K на генерацию
        est_input = (total_q * 1000 + total_g * 2000) / 1_000_000
        est_output = (total_q * 2000 + total_g * 4000) / 1_000_000
        est_cost = est_input * 0.25 + est_output * 1.25

        text += f"**Примерный расход (оценка):**\n"
        text += f"📊 Запросов: {total_q}, Генераций: {total_g}\n"
        text += f"💵 ~${est_cost:.2f} потрачено\n"
        text += f"💵 ~${10 - est_cost:.2f} осталось (из $10)\n\n"
        text += f"⚠️ Это оценка. Точный баланс:\n"
        text += f"👉 console.anthropic.com"

        await query.message.reply_text(text, parse_mode='Markdown')

    elif data == "adm_orders":
        text = "📋 **Заявки на оплату:**\n\n"
        if not user_selected_tariff:
            text += "Новых заявок нет."
        else:
            for uid, tkey in user_selected_tariff.items():
                prourok = load_prourok_data()
                udata = prourok.get(str(uid), {})
                name = udata.get("name", "?")
                tname = TARIFFS.get(tkey, {}).get("name", tkey)
                text += f"• {name} (ID: `{uid}`) → {tname}\n"
                text += f"  Подтвердить: `/confirm {uid}`\n\n"
        await query.message.reply_text(text, parse_mode='Markdown')

    elif data == "adm_help":
        text = (
            "📖 **Команды админа:**\n\n"
            "`/reply ID текст` — ответить пользователю\n"
            "`/confirm ID` — подтвердить оплату (назначит выбранный тариф)\n"
            "`/set_tariff ID тариф` — назначить тариф вручную\n"
            "  Тарифы: demo, start, pro, premium\n"
            "`/add_pack ID` — добавить доп.пакет (+50 запросов)\n"
            "`/reset ID` — сбросить счётчики (новый месяц)\n"
            "`/user_info ID` — инфо о пользователе\n"
            "`/stats` — статистика\n"
            "`/users` — список пользователей\n"
            "`/broadcast текст` — отправить всем (осторожно!)\n"
        )
        await query.message.reply_text(text, parse_mode='Markdown')


# ─── Админ-команды ────────────────────────────────────────────

async def reply_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Формат: `/reply ID текст`", parse_mode='Markdown'); return
    try:
        target = int(context.args[0]); text = ' '.join(context.args[1:])
    except: await update.message.reply_text("⚠️ Неверный ID."); return
    admin_name = ADMIN_NAMES.get(update.effective_user.id, "Специалист")
    try:
        await context.bot.send_message(chat_id=target, text=f"👨‍💻 **{admin_name}:**\n\n{text}", parse_mode='Markdown')
        await update.message.reply_text(f"✅ Отправлено → {target}")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Ошибка: {e}")

async def confirm_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    if not context.args:
        await update.message.reply_text("Формат: `/confirm ID`", parse_mode='Markdown'); return
    try: target = int(context.args[0])
    except: await update.message.reply_text("⚠️ Неверный ID."); return

    tariff_key = user_selected_tariff.get(target, "start")
    tariff = TARIFFS.get(tariff_key, TARIFFS["start"])
    set_user_tariff(target, tariff_key)

    # Убираем из заявок
    user_selected_tariff.pop(target, None)

    try:
        await context.bot.send_message(chat_id=target, text=(
            f"🎉 **Доступ активирован!**\n\n"
            f"📋 Тариф: {tariff['name']} ({tariff['price']})\n"
            f"💬 Запросов: {tariff['queries']}\n"
            f"📄 Оформлений: {tariff['gens']}\n"
            f"📅 Начало: {datetime.now().strftime('%d.%m.%Y')}\n\n"
            f"Откройте бот:\n👉 {PROUROK_BOT_URL}\n\n"
            f"Спасибо что выбрали «ПроУрок»! 🏫"
        ), parse_mode='Markdown')
        await update.message.reply_text(f"✅ Тариф {tariff['name']} активирован для {target}.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Ошибка: {e}")

async def set_tariff_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    if len(context.args) < 2:
        await update.message.reply_text("Формат: `/set_tariff ID тариф`\nТарифы: demo, start, pro, premium", parse_mode='Markdown'); return
    try: target = int(context.args[0])
    except: await update.message.reply_text("⚠️ Неверный ID."); return
    tariff_key = context.args[1].lower()
    if tariff_key not in TARIFFS:
        await update.message.reply_text(f"⚠️ Тариф не найден. Доступны: {', '.join(TARIFFS.keys())}"); return
    set_user_tariff(target, tariff_key)
    tariff = TARIFFS[tariff_key]
    try:
        await context.bot.send_message(chat_id=target, text=(
            f"📋 Ваш тариф изменён!\n\n"
            f"Новый тариф: {tariff['name']} ({tariff['price']})\n"
            f"Счётчики обнулены.\n\n"
            f"👉 {PROUROK_BOT_URL}"
        ), parse_mode='Markdown')
    except: pass
    await update.message.reply_text(f"✅ Тариф {tariff['name']} назначен для {target}.")

async def add_pack_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    if not context.args:
        await update.message.reply_text("Формат: `/add_pack ID`", parse_mode='Markdown'); return
    try: target = int(context.args[0])
    except: await update.message.reply_text("⚠️ Неверный ID."); return
    if add_addon(target):
        try:
            await context.bot.send_message(chat_id=target, text=(
                f"✅ **Доп.пакет активирован!**\n\n"
                f"➕ {ADDON_QUERIES} запросов + {ADDON_GENS} оформлений\n\n"
                f"Нажмите 💰 Баланс в боте чтобы проверить."
            ), parse_mode='Markdown')
        except: pass
        await update.message.reply_text(f"✅ Доп.пакет добавлен для {target}.")
    else:
        await update.message.reply_text(f"⚠️ Пользователь {target} не найден.")

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    if not context.args:
        await update.message.reply_text("Формат: `/reset ID`", parse_mode='Markdown'); return
    try: target = int(context.args[0])
    except: await update.message.reply_text("⚠️ Неверный ID."); return
    data = load_prourok_data()
    uid = str(target)
    if uid in data:
        data[uid]["queries_used"] = 0
        data[uid]["generations_used"] = 0
        save_prourok_data(data)
        await update.message.reply_text(f"✅ Счётчики сброшены для {target}.")
    else:
        await update.message.reply_text(f"⚠️ Не найден.")

async def user_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    if not context.args:
        await update.message.reply_text("Формат: `/user_info ID`", parse_mode='Markdown'); return
    try: target = int(context.args[0])
    except: await update.message.reply_text("⚠️ Неверный ID."); return
    data = load_prourok_data()
    uid = str(target)
    if uid not in data:
        await update.message.reply_text(f"⚠️ Не найден."); return
    u = data[uid]
    tariff = TARIFFS.get(u.get("tariff", "demo"), TARIFFS["demo"])
    text = (
        f"👤 **Пользователь {uid}:**\n\n"
        f"Имя: {u.get('name', '?')}\n"
        f"Username: {u.get('username', '?')}\n"
        f"Регистрация: {u.get('registered', '?')}\n"
        f"Тариф: {tariff['name']} ({tariff['price']})\n"
        f"Тариф с: {u.get('tariff_date', '?')}\n\n"
        f"💬 Запросов: {u.get('queries_used', 0)} / {tariff['queries']}\n"
        f"📄 Оформлений: {u.get('generations_used', 0)} / {tariff['gens']}"
    )
    await update.message.reply_text(text, parse_mode='Markdown')

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    # Имитируем нажатие кнопки stats
    prourok = load_prourok_data()
    total = len(prourok)
    tc = {}; tq = 0; tg = 0
    for uid, ud in prourok.items():
        t = ud.get("tariff", "demo"); tc[t] = tc.get(t, 0) + 1
        tq += ud.get("queries_used", 0); tg += ud.get("generations_used", 0)
    text = f"📊 **Статистика:**\n👥 {total} пользователей\n\n"
    for tk, td in TARIFFS.items(): text += f"{td['name']}: {tc.get(tk, 0)}\n"
    text += f"\n💬 Запросов всего: {tq}\n📄 Оформлений: {tg}"
    est = (tq * 1000 + tg * 2000) / 1e6 * 0.25 + (tq * 2000 + tg * 4000) / 1e6 * 1.25
    text += f"\n💵 ~${est:.2f} потрачено API"
    await update.message.reply_text(text, parse_mode='Markdown')

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    prourok = load_prourok_data()
    if not prourok: await update.message.reply_text("Пусто."); return
    text = "👥 **Пользователи:**\n\n"
    for uid, ud in sorted(prourok.items(), key=lambda x: x[1].get("queries_used", 0), reverse=True)[:30]:
        tn = TARIFFS.get(ud.get("tariff", "demo"), {}).get("name", "?")
        text += f"• {ud.get('name', '?')} — {tn} — 💬{ud.get('queries_used', 0)} 📄{ud.get('generations_used', 0)}\n  `{uid}`\n"
    await update.message.reply_text(text, parse_mode='Markdown')

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    if not context.args:
        await update.message.reply_text("Формат: `/broadcast текст`", parse_mode='Markdown'); return
    text = ' '.join(context.args)
    prourok = load_prourok_data()
    sent = 0; failed = 0
    for uid in prourok:
        try:
            await context.bot.send_message(chat_id=int(uid), text=f"📢 **Объявление:**\n\n{text}", parse_mode='Markdown')
            sent += 1
        except: failed += 1
    await update.message.reply_text(f"✅ Отправлено: {sent}, ошибок: {failed}")


# ─── КЛИЕНТСКАЯ ЧАСТЬ ─────────────────────────────────────────

async def client_start(update, context):
    """Стартовое меню для обычных пользователей."""
    uid = update.effective_user.id
    set_mode(uid, "ai")
    kb = [
        [InlineKeyboardButton("📋 Тарифы", callback_data="tariffs"),
         InlineKeyboardButton("💬 Задать вопрос", callback_data="ask_ai")],
        [InlineKeyboardButton("📖 Как пользоваться", callback_data="how_to"),
         InlineKeyboardButton("👨‍💻 Связаться с человеком", callback_data="call_human")],
        [InlineKeyboardButton("💰 Докупить запросы", callback_data="buy_addon")],
        [InlineKeyboardButton("🚀 Открыть ПроУрок", url=PROUROK_BOT_URL)],
    ]
    await update.message.reply_text(
        "🏫 **Поддержка «ПроУрок»**\n\n"
        "Я помогу разобраться с ботом-помощником.\n\n"
        "💬 Ответить на вопросы\n"
        "📋 Показать тарифы\n"
        "📖 Объяснить как пользоваться\n"
        "👨‍💻 Переключить на специалиста\n\n"
        "Выберите или просто напишите вопрос:",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
    )


# ─── ОБЩИЙ start ──────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in ADMIN_IDS:
        await admin_start(update, context)
    else:
        await client_start(update, context)


# ─── Callback handler ────────────────────────────────────────

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data

    # Админские кнопки
    if data.startswith("adm_") and uid in ADMIN_IDS:
        await admin_callback(update, context, data)
        return

    # ─── ТАРИФЫ ───
    if data == "tariffs":
        text = "📋 **Тарифы «ПроУрок»:**\n\n"
        for key, t in TARIFFS.items():
            if key == "demo": continue
            is_prem = key == "premium"
            ql = "♾ безлимит" if is_prem else str(t['queries'])
            gl = "♾ безлимит" if is_prem else str(t['gens'])
            text += f"**{t['name']}** — {t['price']}\n"
            text += f"  💬 {ql} запросов/мес\n"
            text += f"  📄 {gl} оформлений/мес\n\n"
        text += f"**Доп.пакет:** {ADDON_PRICE} — +{ADDON_QUERIES} запросов, +{ADDON_GENS} оформлений\n\n"
        text += "Выберите тариф:"
        kb = [
            [InlineKeyboardButton("🟢 Старт — 490₽", callback_data="buy_start"),
             InlineKeyboardButton("🔵 Про — 890₽", callback_data="buy_pro")],
            [InlineKeyboardButton("🟡 Премиум — 1 490₽", callback_data="buy_premium")],
            [InlineKeyboardButton("💰 Доп.пакет — 100₽", callback_data="buy_addon")],
            [InlineKeyboardButton("◀️ Назад", callback_data="back_main")],
        ]
        await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

    # ─── ПОКУПКА ТАРИФА ───
    elif data.startswith("buy_") and data != "buy_addon":
        tkey = data[4:]
        tariff = TARIFFS.get(tkey)
        if not tariff: return
        user_selected_tariff[uid] = tkey
        text = (
            f"✅ Тариф: **{tariff['name']}** — {tariff['price']}\n\n"
            f"**Оплата переводом на карту:**\n"
            f"`{CARD_NUMBER}`\n"
            f"Получатель: {CARD_HOLDER}\n"
            f"Сумма: **{tariff['price']}**\n\n"
            "После перевода нажмите кнопку:"
        )
        kb = [
            [InlineKeyboardButton("✅ Я оплатил(а)", callback_data="paid")],
            [InlineKeyboardButton("◀️ К тарифам", callback_data="tariffs")],
        ]
        await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

    # ─── ДОП.ПАКЕТ ───
    elif data == "buy_addon":
        user_selected_tariff[uid] = "addon"
        text = (
            f"💰 **Доп.пакет:**\n\n"
            f"➕ {ADDON_QUERIES} запросов + {ADDON_GENS} оформлений\n"
            f"💵 Стоимость: {ADDON_PRICE}\n\n"
            f"**Переведите на карту:**\n"
            f"`{CARD_NUMBER}`\n"
            f"Получатель: {CARD_HOLDER}\n\n"
            "После перевода нажмите:"
        )
        kb = [
            [InlineKeyboardButton("✅ Я оплатил(а)", callback_data="paid_addon")],
            [InlineKeyboardButton("◀️ Назад", callback_data="tariffs")],
        ]
        await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

    # ─── ОПЛАТА ТАРИФА ───
    elif data == "paid":
        name, uname, uid2 = get_user_info(query)
        tkey = user_selected_tariff.get(uid, "start")
        tariff = TARIFFS.get(tkey, TARIFFS["start"])
        admin_msg = (
            f"💰 **ЗАЯВКА НА ОПЛАТУ!**\n\n"
            f"👤 {name} ({uname})\n"
            f"🆔 `{uid2}`\n"
            f"📋 Тариф: {tariff['name']} — {tariff['price']}\n"
            f"🕐 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
            f"Подтвердить: `/confirm {uid2}`"
        )
        await notify_admins(context, admin_msg)
        await query.message.reply_text(
            "🎉 **Заявка отправлена!**\n\n"
            "Мы проверим оплату и откроем доступ.\n"
            "Обычно в течение часа.\n\n"
            "Вам придёт уведомление здесь.",
            parse_mode='Markdown'
        )

    # ─── ОПЛАТА ДОП.ПАКЕТА ───
    elif data == "paid_addon":
        name, uname, uid2 = get_user_info(query)
        admin_msg = (
            f"💰 **ЗАЯВКА — ДОП.ПАКЕТ!**\n\n"
            f"👤 {name} ({uname})\n"
            f"🆔 `{uid2}`\n"
            f"📋 +{ADDON_QUERIES} запросов, +{ADDON_GENS} оформлений\n"
            f"💵 {ADDON_PRICE}\n"
            f"🕐 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
            f"Подтвердить: `/add_pack {uid2}`"
        )
        await notify_admins(context, admin_msg)
        await query.message.reply_text(
            "🎉 **Заявка на доп.пакет отправлена!**\n\n"
            "Проверим и активируем.",
            parse_mode='Markdown'
        )

    # ─── КАК ПОЛЬЗОВАТЬСЯ ───
    elif data == "how_to":
        text = (
            f"📖 **Как пользоваться:**\n\n"
            f"1️⃣ Откройте {PROUROK_BOT_URL}\n"
            "2️⃣ Нажмите /start\n"
            "3️⃣ Напишите запрос:\n"
            "   • «Тест: дроби, 5 класс, 15 вопросов»\n"
            "   • «План урока: Фотосинтез, биология»\n"
            "4️⃣ Обсуждайте, уточняйте\n"
            "5️⃣ Нажмите кнопку оформления:\n"
            "   📽 Презентация • 📄 Word • 📊 Excel\n\n"
            "**Также:**\n"
            "📸 Фото учебника → анализ\n"
            "🎙 Голосовое → распознаёт и отвечает\n"
            "📄 Документ (Word/PDF) → читает и работает\n"
            "💰 Баланс → сколько осталось"
        )
        kb = [
            [InlineKeyboardButton("🚀 Открыть ПроУрок", url=PROUROK_BOT_URL)],
            [InlineKeyboardButton("◀️ Назад", callback_data="back_main")],
        ]
        await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

    # ─── ЗАДАТЬ ВОПРОС ───
    elif data == "ask_ai":
        set_mode(uid, "ai")
        await query.message.reply_text("💬 Задайте вопрос — я постараюсь помочь!\n\nНапишите «позови человека» если нужен специалист.")

    # ─── ЧЕЛОВЕК ───
    elif data == "call_human":
        set_mode(uid, "human")
        name, uname, uid2 = get_user_info(query)
        await notify_admins(context, f"📩 **Запрос на связь**\n👤 {name} ({uname})\n🆔 `{uid2}`\n\nОтвет: `/reply {uid2} текст`")
        await query.message.reply_text(
            "👨‍💻 **Переключаю на специалиста.**\n\n"
            "Напишите вопрос — ответим скоро.\n"
            "Для возврата к ИИ → /start",
            parse_mode='Markdown'
        )

    # ─── НАЗАД ───
    elif data == "back_main":
        if uid in ADMIN_IDS:
            await admin_start(query, context)
        else:
            kb = [
                [InlineKeyboardButton("📋 Тарифы", callback_data="tariffs"),
                 InlineKeyboardButton("💬 Задать вопрос", callback_data="ask_ai")],
                [InlineKeyboardButton("📖 Как пользоваться", callback_data="how_to"),
                 InlineKeyboardButton("👨‍💻 Связаться с человеком", callback_data="call_human")],
                [InlineKeyboardButton("🚀 Открыть ПроУрок", url=PROUROK_BOT_URL)],
            ]
            await query.message.reply_text("🏫 Выберите:", reply_markup=InlineKeyboardMarkup(kb))


# ─── Обработка сообщений ─────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text

    # Админ
    if uid in ADMIN_IDS:
        await update.message.reply_text(
            "👤 Вы админ. Используйте кнопки из /start или команды.\n\n"
            "Быстрые: `/reply ID текст` • `/confirm ID` • `/stats`",
            parse_mode='Markdown'
        ); return

    mode = get_mode(uid)
    tl = text.lower()

    # Просьба позвать человека
    if any(kw in tl for kw in ['позови человека', 'человек', 'оператор', 'менеджер', 'администратор', 'живой']):
        set_mode(uid, "human")
        name, uname, uid2 = get_user_info(update)
        await notify_admins(context, f"📩 **Просит человека**\n👤 {name} ({uname})\n🆔 `{uid2}`\n💬 «{text}»\n\nОтвет: `/reply {uid2} текст`")
        await update.message.reply_text("👨‍💻 Переключаю. Напишите вопрос — ответим скоро!\nДля ИИ → /start")
        return

    # Режим: человек
    if mode == "human":
        name, uname, uid2 = get_user_info(update)
        await notify_admins(context, f"💬 **Сообщение:**\n👤 {name} ({uname})\n🆔 `{uid2}`\n\n«{text}»\n\nОтвет: `/reply {uid2} текст`")
        await update.message.reply_text("✅ Передано специалисту. Ожидайте.")
        return

    # Режим: ИИ
    answer = ask_support(uid, text)
    kb = [[InlineKeyboardButton("📋 Тарифы", callback_data="tariffs"),
           InlineKeyboardButton("👨‍💻 Человек", callback_data="call_human")]]
    await update.message.reply_text(answer, reply_markup=InlineKeyboardMarkup(kb))


# ─── Ошибки ──────────────────────────────────────────────────

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Ошибка: {context.error}")


# ─── Запуск ──────────────────────────────────────────────────

def main():
    if not TELEGRAM_TOKEN or not ANTHROPIC_API_KEY:
        print("❌ Нет токенов!"); return
    print("🏫 ПроУрок — Поддержка v2.0 запускается...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reply", reply_command))
    app.add_handler(CommandHandler("confirm", confirm_command))
    app.add_handler(CommandHandler("set_tariff", set_tariff_command))
    app.add_handler(CommandHandler("add_pack", add_pack_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("user_info", user_info_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("users", users_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    print("✅ Поддержка v2.0 запущена!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
