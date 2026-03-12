"""
ПроУрок — Web API v3.0
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
import hashlib
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

# ─────────────────────────────────────────────────────────────
# КОНФИГ
# ─────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = "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"
USER_LIMITS_FILE  = "/root/pro-lesson-bot/user_limits.json"
USERS_FILE        = "/root/pro-lesson-bot/users.json"
TOKENS_FILE       = "/root/pro-lesson-bot/auth_tokens.json"
MODEL             = "claude-3-haiku-20240307"
MAX_TOKENS        = 2048

SMTP_EMAIL    = "noreply.prourok@gmail.com"
SMTP_PASSWORD = "mtbyfsgxbnniqylo"
SMTP_HOST     = "smtp.gmail.com"
SMTP_PORT     = 587
BASE_URL      = "http://95.140.147.248"

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
# ПОЛЬЗОВАТЕЛИ
# ─────────────────────────────────────────────────────────────

def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_users(data):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def get_user(email: str):
    users = load_users()
    return users.get(email.lower())

def create_user(email: str, password: str, name="", surname="", patronymic=""):
    users = load_users()
    email = email.lower()
    users[email] = {
        "email": email,
        "password": hash_password(password),
        "name": name,
        "surname": surname,
        "patronymic": patronymic,
        "created": datetime.utcnow().isoformat(),
    }
    save_users(users)
    # Создаём запись в лимитах
    get_user_data(email)

def update_password(email: str, new_password: str):
    users = load_users()
    email = email.lower()
    if email in users:
        users[email]["password"] = hash_password(new_password)
        save_users(users)

# ─────────────────────────────────────────────────────────────
# СЕССИИ
# ─────────────────────────────────────────────────────────────

def load_tokens():
    if not os.path.exists(TOKENS_FILE):
        return {}
    with open(TOKENS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_tokens(data):
    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def create_session(email: str) -> str:
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

def verify_session(token: str):
    tokens = load_tokens()
    if token not in tokens:
        return None
    t = tokens[token]
    if t.get("type") != "session":
        return None
    if datetime.utcnow() > datetime.fromisoformat(t["expires"]):
        return None
    return t["email"]

def create_reset_token(email: str) -> str:
    token = secrets.token_urlsafe(32)
    tokens = load_tokens()
    tokens[token] = {
        "email": email,
        "type": "reset",
        "created": datetime.utcnow().isoformat(),
        "expires": (datetime.utcnow() + timedelta(hours=2)).isoformat(),
        "used": False,
    }
    save_tokens(tokens)
    return token

def verify_reset_token(token: str):
    tokens = load_tokens()
    if token not in tokens:
        return None, "Ссылка недействительна"
    t = tokens[token]
    if t.get("type") != "reset":
        return None, "Неверный тип токена"
    if t.get("used"):
        return None, "Ссылка уже использована"
    if datetime.utcnow() > datetime.fromisoformat(t["expires"]):
        return None, "Ссылка истекла. Запросите новую."
    return t["email"], None

# ─────────────────────────────────────────────────────────────
# EMAIL
# ─────────────────────────────────────────────────────────────

def send_email(to: str, subject: str, html: str) -> bool:
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"ПроУрок <{SMTP_EMAIL}>"
        msg["To"] = to
        msg.attach(MIMEText(html, "html", "utf-8"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_EMAIL, SMTP_PASSWORD)
            s.sendmail(SMTP_EMAIL, to, msg.as_string())
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False

def send_reset_email(email: str, token: str) -> bool:
    link = f"{BASE_URL}/reset.html?token={token}"
    html = f"""
<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#0f1117;font-family:'Helvetica Neue',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0f1117;padding:40px 0;">
  <tr><td align="center">
    <table width="480" cellpadding="0" cellspacing="0" style="background:#181c27;border-radius:16px;border:1px solid #2a3045;">
      <tr><td style="padding:32px;text-align:center;border-bottom:1px solid #2a3045;">
        <div style="font-size:24px;font-weight:700;color:#e8eaf0;">Про<span style="color:#e8c87a;">Урок</span></div>
        <div style="font-size:11px;color:#7a8099;letter-spacing:2px;text-transform:uppercase;margin-top:4px;">AI-помощник учителя</div>
      </td></tr>
      <tr><td style="padding:36px 32px;text-align:center;">
        <p style="color:#e8eaf0;font-size:16px;margin:0 0 8px;">Сброс пароля</p>
        <p style="color:#7a8099;font-size:14px;line-height:1.6;margin:0 0 28px;">
          Нажмите кнопку ниже чтобы задать новый пароль.<br>
          Ссылка действительна <b style="color:#e8eaf0;">2 часа</b>.
        </p>
        <a href="{link}" style="display:inline-block;background:linear-gradient(135deg,#4f7cff,#7c5cfc);color:#fff;text-decoration:none;padding:14px 36px;border-radius:12px;font-size:15px;font-weight:600;">
          Сбросить пароль →
        </a>
        <p style="color:#7a8099;font-size:12px;margin:24px 0 0;">
          Если вы не запрашивали сброс — просто проигнорируйте это письмо.
        </p>
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>"""
    return send_email(email, "Сброс пароля — ПроУрок", html)

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
# МАРШРУТЫ — AUTH
# ─────────────────────────────────────────────────────────────

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "ПроУрок Web API", "version": "3.0"})


@app.route("/api/auth/register", methods=["POST"])
def register():
    body = request.get_json(force=True)
    email    = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", "")).strip()
    name        = str(body.get("name", "")).strip()
    surname     = str(body.get("surname", "")).strip()
    patronymic  = str(body.get("patronymic", "")).strip()

    if not email or "@" not in email:
        return jsonify({"ok": False, "error": "Введите корректный email"}), 400
    if len(password) < 6:
        return jsonify({"ok": False, "error": "Пароль должен быть не менее 6 символов"}), 400
    if get_user(email):
        return jsonify({"ok": False, "error": "Пользователь с таким email уже существует"}), 400

    create_user(email, password, name, surname, patronymic)
    session_token = create_session(email)
    user_data = get_user_data(email)
    tariff = user_data.get("tariff", "demo")
    limits = TARIFF_LIMITS.get(tariff, TARIFF_LIMITS["demo"])

    return jsonify({
        "ok": True,
        "session_token": session_token,
        "email": email,
        "name": name or email.split("@")[0],
        "tariff": tariff,
        "queries_used": user_data.get("queries_used", 0),
        "queries_limit": limits["queries"],
    })


@app.route("/api/auth/login", methods=["POST"])
def login():
    body = request.get_json(force=True)
    email    = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", "")).strip()

    if not email or not password:
        return jsonify({"ok": False, "error": "Введите email и пароль"}), 400

    user = get_user(email)
    if not user:
        return jsonify({"ok": False, "error": "Пользователь не зарегистрирован", "not_found": True}), 404
    if user["password"] != hash_password(password):
        return jsonify({"ok": False, "error": "Неверный email или пароль"}), 401

    session_token = create_session(email)
    user_data = get_user_data(email)
    tariff = user_data.get("tariff", "demo")
    limits = TARIFF_LIMITS.get(tariff, TARIFF_LIMITS["demo"])
    name = user.get("name") or email.split("@")[0]

    return jsonify({
        "ok": True,
        "session_token": session_token,
        "email": email,
        "name": name,
        "tariff": tariff,
        "queries_used": user_data.get("queries_used", 0),
        "queries_limit": limits["queries"],
    })


@app.route("/api/auth/me", methods=["GET"])
def auth_me():
    token = request.headers.get("X-Session-Token", "")
    email = verify_session(token)
    if not email:
        return jsonify({"ok": False, "error": "Не авторизован"}), 401

    user = get_user(email)
    user_data = get_user_data(email)
    tariff = user_data.get("tariff", "demo")
    limits = TARIFF_LIMITS.get(tariff, TARIFF_LIMITS["demo"])
    name = (user.get("name") if user else None) or email.split("@")[0]

    return jsonify({
        "ok": True,
        "email": email,
        "name": name,
        "tariff": tariff,
        "queries_used": user_data.get("queries_used", 0),
        "queries_limit": limits["queries"],
        "generations_used": user_data.get("generations_used", 0),
        "generations_limit": limits["generations"],
    })


@app.route("/api/auth/reset/request", methods=["POST"])
def reset_request():
    body = request.get_json(force=True)
    email = str(body.get("email", "")).strip().lower()

    if not email or "@" not in email:
        return jsonify({"ok": False, "error": "Введите корректный email"}), 400
    if not get_user(email):
        return jsonify({"ok": False, "error": "Пользователь с таким email не найден"}), 404

    token = create_reset_token(email)
    sent = send_reset_email(email, token)
    if sent:
        return jsonify({"ok": True, "message": f"Письмо отправлено на {email}"})
    return jsonify({"ok": False, "error": "Не удалось отправить письмо"}), 500


@app.route("/api/auth/reset/confirm", methods=["POST"])
def reset_confirm():
    body = request.get_json(force=True)
    token    = str(body.get("token", "")).strip()
    password = str(body.get("password", "")).strip()

    if len(password) < 6:
        return jsonify({"ok": False, "error": "Пароль должен быть не менее 6 символов"}), 400

    email, err = verify_reset_token(token)
    if err:
        return jsonify({"ok": False, "error": err}), 401

    update_password(email, password)
    tokens = load_tokens()
    tokens[token]["used"] = True
    save_tokens(tokens)

    return jsonify({"ok": True, "message": "Пароль успешно изменён"})


# ─────────────────────────────────────────────────────────────
# МАРШРУТЫ — ЧАТ
# ─────────────────────────────────────────────────────────────

@app.route("/api/chat", methods=["POST"])
def chat():
    session_token = request.headers.get("X-Session-Token", "")
    email = verify_session(session_token)
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
            model=MODEL, max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT, messages=messages
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
    email = verify_session(session_token)
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
    print("ПроУрок Web API v3.0 — порт 5001")
    app.run(host="0.0.0.0", port=5001, debug=False)
