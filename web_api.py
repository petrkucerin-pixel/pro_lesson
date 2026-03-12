"""
ПроУрок — Web API
Файл:    /root/pro-lesson-bot/web_api.py
Запуск:  python3 web_api.py
Порт:    5001

Telegram бот (bot.py) продолжает работать независимо на своём порту.
Оба используют один user_limits.json и один API-ключ.
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import anthropic
import json
import re
import os

# ─────────────────────────────────────────────────────────────
# КОНФИГ
# ─────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = "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"
USER_LIMITS_FILE  = "/root/pro-lesson-bot/user_limits.json"
MODEL             = "claude-3-haiku-20240307"
MAX_TOKENS        = 2048

TARIFF_LIMITS = {
    "demo":    {"queries": 20,     "generations": 5},
    "start":   {"queries": 200,    "generations": 30},
    "pro":     {"queries": 500,    "generations": 100},
    "premium": {"queries": 999999, "generations": 999999},
}

SYSTEM_PROMPT = """Ты — ПроУрок, AI-помощник учителя. Помогаешь составлять планы уроков, тесты, конспекты и другие учебные материалы строго по ФГОС ООО (Приказ №287 от 31.05.2021).

Правила:
- Всегда учитывай актуальные нормативные документы
- Структурируй ответы чётко, с заголовками и списками
- Добавляй УУД (универсальные учебные действия) где уместно
- Для истории: упоминай все три курса — История России, Всеобщая история, История нашего края (обязателен с 01.09.2025 по Приказу №110)
- Для географии: добавляй краеведческий компонент — связь темы с географией своего региона
- Отвечай только на русском языке
- Будь конкретным и практичным — учитель должен сразу использовать материал"""

# ─────────────────────────────────────────────────────────────
# ИНИЦИАЛИЗАЦИЯ
# ─────────────────────────────────────────────────────────────

app = Flask(__name__)
CORS(app)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ─────────────────────────────────────────────────────────────
# РАБОТА С ЛИМИТАМИ
# ─────────────────────────────────────────────────────────────

def load_limits():
    if not os.path.exists(USER_LIMITS_FILE):
        return {}
    with open(USER_LIMITS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_limits(data):
    with open(USER_LIMITS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_user_data(user_id: str) -> dict:
    data = load_limits()
    if user_id not in data:
        data[user_id] = {"tariff": "demo", "queries_used": 0, "generations_used": 0}
        save_limits(data)
    return data[user_id]

def check_limit(user_id: str):
    user = get_user_data(user_id)
    tariff = user.get("tariff", "demo")
    used = user.get("queries_used", 0)
    limit = TARIFF_LIMITS.get(tariff, TARIFF_LIMITS["demo"])["queries"]
    if used >= limit:
        return False, f"Лимит запросов исчерпан ({used}/{limit}). Тариф: {tariff}."
    return True, ""

def increment_query(user_id: str):
    data = load_limits()
    if user_id not in data:
        data[user_id] = {"tariff": "demo", "queries_used": 0, "generations_used": 0}
    data[user_id]["queries_used"] = data[user_id].get("queries_used", 0) + 1
    save_limits(data)

# ─────────────────────────────────────────────────────────────
# ПРЕДОБРАБОТКА (копия логики из bot.py v1.5)
# ─────────────────────────────────────────────────────────────

def preprocess_message(text: str):
    lower = text.lower()

    # МАТЕМАТИКА — просим уточнить курс
    math_keywords = ["математик", "контрольн", "тест по матем", "урок матем", "план матем"]
    math_specific = ["алгебр", "геометр", "вероятност", "статистик"]
    if any(k in lower for k in math_keywords):
        if not any(k in lower for k in math_specific):
            return text, (
                "⚠️ Уточните курс математики:\n\n"
                "• Алгебра\n• Геометрия\n• Вероятность и статистика\n\n"
                "Добавьте уточнение в запрос, например: «план урока по алгебре 8 класс»"
            )

    # ОБЖ → ОБЗР
    text = re.sub(r'\bобж\b', 'Основы безопасности и защиты Родины', text, flags=re.IGNORECASE)

    # ТЕХНОЛОГИЯ → Труд
    text = re.sub(r'\bтехнологи[яиюей]\b', 'Труд (технология)', text, flags=re.IGNORECASE)

    # ИСТОРИЯ — три курса
    if any(k in lower for k in ["истори", "по истор", "урок истор", "план истор"]):
        text += (
            "\n\n[Инструкция: Предмет История включает ТРИ курса: "
            "История России, Всеобщая история, История нашего края (обязателен с 01.09.2025). "
            "Упомяни все три. Добавь региональный компонент.]"
        )

    # ГЕОГРАФИЯ — краеведение
    if any(k in lower for k in ["географи", "по геогр", "урок геогр", "план геогр"]):
        text += (
            "\n\n[Инструкция: По ФГОС добавь краеведческий компонент — "
            "как тема связана с географией своего региона/края.]"
        )

    return text, None

# ─────────────────────────────────────────────────────────────
# МАРШРУТЫ
# ─────────────────────────────────────────────────────────────

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "ПроУрок Web API", "version": "1.5"})


@app.route("/api/chat", methods=["POST"])
def chat():
    """
    POST /api/chat
    Body: { "user_id": "web_123", "message": "...", "history": [...] }
    """
    try:
        body = request.get_json(force=True)
    except Exception:
        return jsonify({"ok": False, "error": "Некорректный JSON"}), 400

    user_id = str(body.get("user_id", "")).strip()
    message = str(body.get("message", "")).strip()
    history = body.get("history", [])

    if not user_id:
        return jsonify({"ok": False, "error": "user_id обязателен"}), 400
    if not message:
        return jsonify({"ok": False, "error": "Сообщение не может быть пустым"}), 400
    if len(message) > 4000:
        return jsonify({"ok": False, "error": "Сообщение слишком длинное (макс. 4000 символов)"}), 400

    # Лимит
    allowed, limit_msg = check_limit(user_id)
    if not allowed:
        return jsonify({"ok": False, "error": limit_msg, "limit_exceeded": True}), 403

    # Предобработка
    processed, clarification = preprocess_message(message)
    if clarification:
        return jsonify({"ok": True, "reply": clarification, "clarification": True})

    # История диалога (последние 10)
    messages = []
    for item in history[-10:]:
        role = item.get("role")
        content = item.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": processed})

    # Claude
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=messages
        )
        reply = response.content[0].text
    except anthropic.APIError as e:
        return jsonify({"ok": False, "error": f"Ошибка Claude API: {str(e)}"}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": f"Внутренняя ошибка: {str(e)}"}), 500

    increment_query(user_id)

    user_data = get_user_data(user_id)
    tariff = user_data.get("tariff", "demo")

    return jsonify({
        "ok": True,
        "reply": reply,
        "queries_used": user_data.get("queries_used", 0),
        "queries_limit": TARIFF_LIMITS.get(tariff, TARIFF_LIMITS["demo"])["queries"],
        "tariff": tariff
    })


@app.route("/api/balance", methods=["GET"])
def balance():
    """GET /api/balance?user_id=web_123"""
    user_id = request.args.get("user_id", "").strip()
    if not user_id:
        return jsonify({"ok": False, "error": "user_id обязателен"}), 400

    user_data = get_user_data(user_id)
    tariff = user_data.get("tariff", "demo")
    limits = TARIFF_LIMITS.get(tariff, TARIFF_LIMITS["demo"])

    return jsonify({
        "ok": True,
        "tariff": tariff,
        "queries_used": user_data.get("queries_used", 0),
        "queries_limit": limits["queries"],
        "generations_used": user_data.get("generations_used", 0),
        "generations_limit": limits["generations"]
    })


# ─────────────────────────────────────────────────────────────
# ЗАПУСК
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("ПроУрок Web API — порт 5001")
    app.run(host="0.0.0.0", port=5001, debug=False)
