"""
ПроУрок — Web API v2.0
Файл:    /root/pro-lesson-bot/web_api.py
Порт:    5001
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import anthropic
import json
import re
import os
import secrets
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

# ─────────────────────────────────────────────────────────────
# КОНФИГ
# ─────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = "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"
USER_LIMITS_FILE  = "/root/pro-lesson-bot/user_limits.json"
TOKENS_FILE       = "/root/pro-lesson-bot/auth_tokens.json"
MODEL             = "claude-3-haiku-20240307"
MAX_TOKENS        = 2048

SMTP_EMAIL    = "noreply.prourok@gmail.com"
SMTP_PASSWORD = "mtbyfsgxbnniqylo"
SMTP_HOST     = "smtp.gmail.com"
SMTP_PORT     = 587

BASE_URL = "http://95.140.147.248"

TOKEN_EXPIRE_HOURS = 24

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

app = Flask(__name__)
CORS(app)
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ─────────────────────────────────────────────────────────────
# ТОКЕНЫ АВТОРИЗАЦИИ
# ─────────────────────────────────────────────────────────────

def load_tokens():
    if not os.path.exists(TOKENS_FILE):
        return {}
    with open(TOKENS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_tokens(data):
    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def create_magic_token(email: str) -> str:
    token = secrets.token_urlsafe(32)
    tokens = load_tokens()
    tokens[token] = {
        "email": email,
        "created": datetime.utcnow().isoformat(),
        "expires": (datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)).isoformat(),
        "used": False
    }
    save_tokens(tokens)
    return token

def verify_session_token(token: str):
    """Проверяет сессионный токен, возвращает email или None."""
    tokens = load_tokens()
    if token not in tokens:
        return None
    t = tokens[token]
    if t.get("type") != "session":
        return None
    expires = datetime.fromisoformat(t["expires"])
    if datetime.utcnow() > expires:
        return None
    return t["email"]

def create_session_token(email: str) -> str:
    token = secrets.token_urlsafe(32)
    tokens = load_tokens()
    tokens[token] = {
        "email": email,
        "type": "session",
        "created": datetime.utcnow().isoformat(),
        "expires": (datetime.utcnow() + timedelta(days=30)).isoformat(),
    }
    save_tokens(tokens)
    return token

# ─────────────────────────────────────────────────────────────
# ОТПРАВКА EMAIL
# ─────────────────────────────────────────────────────────────

def send_magic_link(email: str, token: str) -> bool:
    link = f"{BASE_URL}/auth?token={token}"
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Вход в ПроУрок"
        msg["From"] = f"ПроУрок <{SMTP_EMAIL}>"
        msg["To"] = email

        html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#0f1117;font-family:'Helvetica Neue',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0f1117;padding:40px 0;">
    <tr><td align="center">
      <table width="480" cellpadding="0" cellspacing="0" style="background:#181c27;border-radius:16px;border:1px solid #2a3045;overflow:hidden;">
        <tr>
          <td style="padding:32px;text-align:center;background:linear-gradient(135deg,#181c27,#1e2435);border-bottom:1px solid #2a3045;">
            <div style="font-size:42px;margin-bottom:8px;">📚</div>
            <div style="font-size:24px;font-weight:700;color:#e8eaf0;letter-spacing:-0.5px;">Про<span style="color:#e8c87a;">Урок</span></div>
            <div style="font-size:11px;color:#7a8099;letter-spacing:2px;text-transform:uppercase;margin-top:4px;">AI-помощник учителя</div>
          </td>
        </tr>
        <tr>
          <td style="padding:36px 32px;text-align:center;">
            <p style="color:#e8eaf0;font-size:16px;margin:0 0 8px;">Привет!</p>
            <p style="color:#7a8099;font-size:14px;line-height:1.6;margin:0 0 28px;">
              Нажмите кнопку ниже чтобы войти в ПроУрок.<br>
              Ссылка действительна <b style="color:#e8eaf0;">24 часа</b>.
            </p>
            <a href="{link}" style="display:inline-block;background:linear-gradient(135deg,#4f7cff,#7c5cfc);color:#fff;text-decoration:none;padding:14px 36px;border-radius:12px;font-size:15px;font-weight:600;letter-spacing:0.3px;">
              Войти в ПроУрок →
            </a>
            <p style="color:#7a8099;font-size:12px;margin:24px 0 0;line-height:1.6;">
              Если кнопка не работает, скопируйте ссылку:<br>
              <span style="color:#4f7cff;font-size:11px;">{link}</span>
            </p>
          </td>
        </tr>
        <tr>
          <td style="padding:16px 32px;text-align:center;border-top:1px solid #2a3045;">
            <p style="color:#7a8099;font-size:11px;margin:0;">
              Если вы не запрашивали вход — просто проигнорируйте это письмо.
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
        """

        msg.attach(MIMEText(html, "html", "utf-8"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.sendmail(SMTP_EMAIL, email, msg.as_string())
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False

# ─────────────────────────────────────────────────────────────
# ЛИМИТЫ
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
# ПРЕДОБРАБОТКА
# ─────────────────────────────────────────────────────────────

def preprocess_message(text: str):
    lower = text.lower()
    math_keywords = ["математик", "контрольн", "тест по матем", "урок матем", "план матем"]
    math_specific = ["алгебр", "геометр", "вероятност", "статистик"]
    if any(k in lower for k in math_keywords):
        if not any(k in lower for k in math_specific):
            return text, "⚠️ Уточните курс математики:\n\n• Алгебра\n• Геометрия\n• Вероятность и статистика\n\nДобавьте уточнение в запрос, например: «план урока по алгебре 8 класс»"

    text = re.sub(r'\bобж\b', 'Основы безопасности и защиты Родины', text, flags=re.IGNORECASE)
    text = re.sub(r'\bтехнологи[яиюей]\b', 'Труд (технология)', text, flags=re.IGNORECASE)

    if any(k in lower for k in ["истори", "по истор", "урок истор", "план истор"]):
        text += "\n\n[Инструкция: Предмет История включает ТРИ курса: История России, Всеобщая история, История нашего края (обязателен с 01.09.2025). Упомяни все три. Добавь региональный компонент.]"

    if any(k in lower for k in ["географи", "по геогр", "урок геогр", "план геогр"]):
        text += "\n\n[Инструкция: По ФГОС добавь краеведческий компонент — как тема связана с географией своего региона/края.]"

    return text, None

# ─────────────────────────────────────────────────────────────
# МАРШРУТЫ
# ─────────────────────────────────────────────────────────────

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "ПроУрок Web API", "version": "2.0"})


@app.route("/api/auth/request", methods=["POST"])
def auth_request():
    """Отправляет магическую ссылку на email."""
    body = request.get_json(force=True)
    email = str(body.get("email", "")).strip().lower()

    if not email or "@" not in email:
        return jsonify({"ok": False, "error": "Введите корректный email"}), 400

    token = create_magic_token(email)
    sent = send_magic_link(email, token)

    if sent:
        return jsonify({"ok": True, "message": f"Ссылка отправлена на {email}"})
    else:
        return jsonify({"ok": False, "error": "Не удалось отправить письмо. Попробуйте позже."}), 500


@app.route("/api/auth/verify", methods=["POST"])
def auth_verify():
    """Проверяет магический токен и создаёт сессию."""
    body = request.get_json(force=True)
    token = str(body.get("token", "")).strip()

    tokens = load_tokens()
    if token not in tokens:
        return jsonify({"ok": False, "error": "Ссылка недействительна"}), 401

    t = tokens[token]
    if t.get("used"):
        return jsonify({"ok": False, "error": "Ссылка уже использована"}), 401
    if t.get("type") == "session":
        return jsonify({"ok": False, "error": "Неверный тип токена"}), 401

    expires = datetime.fromisoformat(t["expires"])
    if datetime.utcnow() > expires:
        return jsonify({"ok": False, "error": "Ссылка истекла. Запросите новую."}), 401

    # Помечаем как использованный
    tokens[token]["used"] = True
    save_tokens(tokens)

    email = t["email"]
    session_token = create_session_token(email)

    # Убедимся что пользователь есть в лимитах
    get_user_data(email)

    return jsonify({"ok": True, "session_token": session_token, "email": email})


@app.route("/api/auth/me", methods=["GET"])
def auth_me():
    """Проверяет сессию."""
    token = request.headers.get("X-Session-Token", "")
    email = verify_session_token(token)
    if not email:
        return jsonify({"ok": False, "error": "Не авторизован"}), 401

    user_data = get_user_data(email)
    tariff = user_data.get("tariff", "demo")
    limits = TARIFF_LIMITS.get(tariff, TARIFF_LIMITS["demo"])

    return jsonify({
        "ok": True,
        "email": email,
        "tariff": tariff,
        "queries_used": user_data.get("queries_used", 0),
        "queries_limit": limits["queries"],
        "generations_used": user_data.get("generations_used", 0),
        "generations_limit": limits["generations"]
    })


@app.route("/api/chat", methods=["POST"])
def chat():
    # Проверка сессии
    session_token = request.headers.get("X-Session-Token", "")
    email = verify_session_token(session_token)
    if not email:
        return jsonify({"ok": False, "error": "Не авторизован", "unauthorized": True}), 401

    body = request.get_json(force=True)
    message = str(body.get("message", "")).strip()
    history = body.get("history", [])

    if not message:
        return jsonify({"ok": False, "error": "Сообщение не может быть пустым"}), 400
    if len(message) > 4000:
        return jsonify({"ok": False, "error": "Сообщение слишком длинное"}), 400

    allowed, limit_msg = check_limit(email)
    if not allowed:
        return jsonify({"ok": False, "error": limit_msg, "limit_exceeded": True}), 403

    processed, clarification = preprocess_message(message)
    if clarification:
        return jsonify({"ok": True, "reply": clarification, "clarification": True})

    messages = []
    for item in history[-10:]:
        role = item.get("role")
        content = item.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": processed})

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

    increment_query(email)
    user_data = get_user_data(email)
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
    session_token = request.headers.get("X-Session-Token", "")
    email = verify_session_token(session_token)
    if not email:
        return jsonify({"ok": False, "error": "Не авторизован"}), 401

    user_data = get_user_data(email)
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


if __name__ == "__main__":
    print("ПроУрок Web API v2.0 — порт 5001")
    app.run(host="0.0.0.0", port=5001, debug=False)
