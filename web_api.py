"""
ПроУрок — Web API v3.2
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
import requests as http_requests
from datetime import datetime, timedelta
import threading
from dotenv import load_dotenv
from fgos_context import FGOS_MAP

# ─────────────────────────────────────────────────────────────
# КОНФИГ
# ─────────────────────────────────────────────────────────────

load_dotenv()  # читает /root/pro-lesson-bot/.env

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
RESEND_API_KEY    = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL        = "ПроУрок <noreply@pro-urok.ru>"

USER_LIMITS_FILE  = "/root/pro-lesson-bot/user_limits.json"
USERS_FILE        = "/root/pro-lesson-bot/users.json"
TOKENS_FILE       = "/root/pro-lesson-bot/auth_tokens.json"
KOPILKA_FILE      = "/root/pro-lesson-bot/kopilka.json"
MODEL             = "claude-3-haiku-20240307"
MAX_TOKENS        = 2048
MODEL_GEN         = "claude-haiku-4-5-20251001"
MAX_TOKENS_GEN    = 4096

BASE_URL  = "https://pro-urok.ru"


TARIFF_LIMITS = {
    "demo":    {"queries": 20,     "generations": 5},
    "start":   {"queries": 200,    "generations": 30},
    "pro":     {"queries": 500,    "generations": 100},
    "premium": {"queries": 999999, "generations": 999999},
}

def get_fgos_level(grade):
    try:
        g = int(grade)
    except (TypeError, ValueError):
        return None
    if 1 <= g <= 4: return 'НОО'
    elif 5 <= g <= 9: return 'ООО'
    elif 10 <= g <= 11: return 'СОО'
    return None

def build_system_prompt(base_prompt, grade=None):
    level = get_fgos_level(grade)
    if level and level in FGOS_MAP:
        fgos_block = (
            f"\n\n=== ФГОС {level} (класс {grade}) ===\n"
            f"{FGOS_MAP[level]}\n"
            f"ОБЯЗАТЕЛЬНО: цели в деятельностной форме, три вида результатов, УУД по уровню.\n"
            f"=== КОНЕЦ ФГОС ==="
        )
        return base_prompt + fgos_block
    return base_prompt


SYSTEM_PROMPT = """Ты — ПроУрок, AI-помощник учителя. Помогаешь составлять планы уроков, тесты, конспекты и другие учебные материалы строго по ФГОС.

ВАЖНО: Никогда не задавай уточняющих вопросов. Всегда создавай готовый материал сразу на основе полученных данных. Если данных достаточно для создания материала — создавай. Учитель ждёт готовый результат, а не диалог.

Правила:
- Всегда учитывай актуальные нормативные документы
- Структурируй ответы чётко, с заголовками и списками
- Добавляй УУД (универсальные учебные действия) где уместно
- Для математики 1-6 класс: предмет называется «Математика» (единый курс, без разделения на алгебру и геометрию)
- Для математики 7-9 класс: уточняй в рамках предмета — Алгебра или Геометрия, если не указано — составляй по Алгебре
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
        "confirmed": False,
    }
    save_users(users)
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

def create_confirm_token(email: str) -> str:
    token = secrets.token_urlsafe(32)
    tokens = load_tokens()
    tokens[token] = {
        "email": email,
        "type": "confirm",
        "created": datetime.utcnow().isoformat(),
        "expires": (datetime.utcnow() + timedelta(hours=48)).isoformat(),
        "used": False,
    }
    save_tokens(tokens)
    return token

def verify_confirm_token(token: str):
    tokens = load_tokens()
    if token not in tokens:
        return None, "Ссылка недействительна"
    t = tokens[token]
    if t.get("type") != "confirm":
        return None, "Неверный тип токена"
    if t.get("used"):
        return None, "Ссылка уже использована"
    if datetime.utcnow() > datetime.fromisoformat(t["expires"]):
        return None, "Ссылка истекла. Зарегистрируйтесь заново."
    return t["email"], None

def confirm_user(email: str):
    users = load_users()
    if email in users:
        users[email]["confirmed"] = True
        save_users(users)

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

def _send_email_sync(to: str, subject: str, html: str):
    try:
        resp = http_requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"from": FROM_EMAIL, "to": [to], "subject": subject, "html": html},
            timeout=15,
        )
        if resp.status_code == 200 or resp.status_code == 201:
            print(f"Email sent to {to}")
        else:
            print(f"Email error {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"Email error: {e}")

def send_email(to: str, subject: str, html: str) -> bool:
    t = threading.Thread(target=_send_email_sync, args=(to, subject, html), daemon=True)
    t.start()
    return True

def send_reset_email(email: str, token: str) -> bool:
    link = f"{BASE_URL}/reset.html?token={token}"
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#0f1117;font-family:'Helvetica Neue',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0f1117;padding:40px 0;">
  <tr><td align="center">
    <table width="480" cellpadding="0" cellspacing="0" style="background:#181c27;border-radius:16px;border:1px solid #2a3045;">
      <tr><td style="padding:32px;text-align:center;border-bottom:1px solid #2a3045;">
        <div style="font-size:24px;font-weight:700;color:#e8eaf0;">Про<span style="color:#e8c87a;">Урок</span></div>
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
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>"""
    return send_email(email, "Сброс пароля — ПроУрок", html)

def send_confirm_email(email: str, token: str) -> bool:
    link = f"{BASE_URL}/confirm.html?token={token}"
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#0f1117;font-family:'Helvetica Neue',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0f1117;padding:40px 0;">
  <tr><td align="center">
    <table width="480" cellpadding="0" cellspacing="0" style="background:#181c27;border-radius:16px;border:1px solid #2a3045;">
      <tr><td style="padding:32px;text-align:center;border-bottom:1px solid #2a3045;">
        <div style="font-size:24px;font-weight:700;color:#e8eaf0;">Про<span style="color:#e8c87a;">Урок</span></div>
      </td></tr>
      <tr><td style="padding:36px 32px;text-align:center;">
        <p style="color:#e8eaf0;font-size:16px;margin:0 0 8px;">Подтвердите email</p>
        <p style="color:#7a8099;font-size:14px;line-height:1.6;margin:0 0 28px;">
          Нажмите кнопку ниже чтобы подтвердить регистрацию.<br>
          Ссылка действительна <b style="color:#e8eaf0;">48 часов</b>.
        </p>
        <a href="{link}" style="display:inline-block;background:linear-gradient(135deg,#4f7cff,#7c5cfc);color:#fff;text-decoration:none;padding:14px 36px;border-radius:12px;font-size:15px;font-weight:600;">
          Подтвердить email →
        </a>
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>"""
    return send_email(email, "Подтвердите регистрацию — ПроУрок", html)

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

def preprocess_message(text: str, grade=None):
    lower = text.lower()
    level = get_fgos_level(grade)

    # НОО: алгебра/геометрия не существуют как отдельные предметы
    if level == 'НОО':
        noo_math_specific = ["алгебр", "геометр"]
        if any(k in lower for k in noo_math_specific):
            return text, "⚠️ В 1–4 классе (НОО) математика изучается как единый курс «Математика» — без разделения на алгебру и геометрию.\n\nПопробуйте: «план урока по математике, {grade} класс»"

    # СОО: напомнить про профильное обучение
    if level == 'СОО':
        profile_keywords = ["профил", "естественнонауч", "гуманитар", "социально-эконом", "технологич", "агротехнолог", "универсал"]
        if not any(k in lower for k in profile_keywords):
            text = text + "\n[Примечание: класс 10–11, СОО — если требуется, уточни профиль обучения: естественно-научный, гуманитарный, социально-экономический, технологический, агротехнологический или универсальный]"

    math_keywords = ["математик", "контрольн", "тест по матем", "урок матем", "план матем"]
    math_specific = ["алгебр", "геометр", "вероятност", "статистик"]
    # Не задавать уточнение если: уже указан класс 1-6, идёт редактирование, или есть примечание от фронтенда
    early_grade_markers = [f"{n} класс" for n in range(1, 7)] + ["единый курс", "нераздельный", "примечание:", "редактир", "редактируй", "изменить", "обновлённый"]
    if any(k in lower for k in math_keywords):
        if not any(k in lower for k in math_specific):
            if not any(m in lower for m in early_grade_markers):
                if level != 'НОО':  # для НОО уже обработано выше
                    return text, "⚠️ Уточните курс математики:\n\n• Алгебра\n• Геометрия\n• Вероятность и статистика\n\nДобавьте уточнение в запрос, например: «план урока по алгебре 8 класс»"
    text = re.sub(r'\bобж\b', 'Основы безопасности и защиты Родины', text, flags=re.IGNORECASE)
    text = re.sub(r'\bтехнологи[яиюей]\b', 'Труд (технология)', text, flags=re.IGNORECASE)
    if any(k in lower for k in ["истори", "по истор", "урок истор", "план истор"]):
        text += "\n\n[Инструкция: Предмет История включает ТРИ курса: История России, Всеобщая история, История нашего края (обязателен с 01.09.2025). Упомяни все три. Добавь региональный компонент.]"
    if any(k in lower for k in ["географи", "по геогр", "урок геогр", "план геогр"]):
        text += "\n\n[Инструкция: По ФГОС добавь краеведческий компонент — как тема связана с географией своего региона/края.]"
    return text, None

# ─────────────────────────────────────────────────────────────
# КОПИЛКА
# ─────────────────────────────────────────────────────────────

def load_kopilka():
    if not os.path.exists(KOPILKA_FILE):
        return {}
    with open(KOPILKA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_kopilka(data):
    with open(KOPILKA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@app.route("/api/kopilka/save", methods=["POST"])
def kopilka_save():
    session_token = request.headers.get("X-Session-Token", "")
    email = verify_session(session_token)
    if not email:
        return jsonify({"ok": False, "error": "Не авторизован"}), 401
    body        = request.get_json(force=True)
    item_id     = body.get("item_id")
    title       = str(body.get("title", "")).strip()
    content     = str(body.get("content", "")).strip()
    item_type   = str(body.get("type", "other")).strip()
    subject     = str(body.get("subject", "")).strip()
    grade       = str(body.get("grade", "")).strip()
    lesson_type = str(body.get("lesson_type", "")).strip()
    if not content:
        return jsonify({"ok": False, "error": "Содержимое не может быть пустым"}), 400
    kopilka = load_kopilka()
    if email not in kopilka:
        kopilka[email] = {}
    user_items = kopilka[email]
    if item_id and str(item_id) in user_items:
        item = user_items[str(item_id)]
        item["title"]       = title or item["title"]
        item["content"]     = content
        item["type"]        = item_type
        item["subject"]     = subject
        item["grade"]       = grade
        item["lesson_type"] = lesson_type
        item["updated"]     = datetime.utcnow().isoformat()
    else:
        new_id = str(max((int(k) for k in user_items.keys()), default=0) + 1)
        item_id = new_id
        user_items[new_id] = {
            "id": new_id, "title": title or f"Материал №{new_id}",
            "content": content, "type": item_type, "subject": subject,
            "grade": grade, "lesson_type": lesson_type,
            "created": datetime.utcnow().isoformat(), "updated": datetime.utcnow().isoformat(),
        }
    save_kopilka(kopilka)
    return jsonify({"ok": True, "item_id": str(item_id), "message": "Сохранено в копилку"})


@app.route("/api/kopilka/list", methods=["GET"])
def kopilka_list():
    session_token = request.headers.get("X-Session-Token", "")
    email = verify_session(session_token)
    if not email:
        return jsonify({"ok": False, "error": "Не авторизован"}), 401
    kopilka = load_kopilka()
    user_items = kopilka.get(email, {})
    items = sorted(user_items.values(), key=lambda x: int(x["id"]))
    preview_items = [{
        "id": it["id"], "title": it["title"], "type": it["type"],
        "subject": it["subject"], "grade": it["grade"], "lesson_type": it["lesson_type"],
        "created": it["created"], "updated": it["updated"],
        "preview": it["content"][:120] + "..." if len(it["content"]) > 120 else it["content"],
    } for it in items]
    return jsonify({"ok": True, "items": preview_items, "total": len(preview_items)})


@app.route("/api/kopilka/get/<item_id>", methods=["GET"])
def kopilka_get(item_id):
    session_token = request.headers.get("X-Session-Token", "")
    email = verify_session(session_token)
    if not email:
        return jsonify({"ok": False, "error": "Не авторизован"}), 401
    kopilka = load_kopilka()
    item = kopilka.get(email, {}).get(str(item_id))
    if not item:
        return jsonify({"ok": False, "error": f"Материал №{item_id} не найден"}), 404
    return jsonify({"ok": True, "item": item})


@app.route("/api/kopilka/delete/<item_id>", methods=["DELETE"])
def kopilka_delete(item_id):
    session_token = request.headers.get("X-Session-Token", "")
    email = verify_session(session_token)
    if not email:
        return jsonify({"ok": False, "error": "Не авторизован"}), 401
    kopilka = load_kopilka()
    user_items = kopilka.get(email, {})
    if str(item_id) not in user_items:
        return jsonify({"ok": False, "error": "Материал не найден"}), 404
    del user_items[str(item_id)]
    save_kopilka(kopilka)
    return jsonify({"ok": True, "message": f"Материал №{item_id} удалён"})

# ─────────────────────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────────────────────

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "ПроУрок Web API", "version": "3.1"})


@app.route("/api/auth/register", methods=["POST"])
def register():
    body       = request.get_json(force=True)
    email      = str(body.get("email", "")).strip().lower()
    password   = str(body.get("password", "")).strip()
    name       = str(body.get("name", "")).strip()
    surname    = str(body.get("surname", "")).strip()
    patronymic = str(body.get("patronymic", "")).strip()
    if not email or "@" not in email:
        return jsonify({"ok": False, "error": "Введите корректный email"}), 400
    if len(password) < 6:
        return jsonify({"ok": False, "error": "Пароль должен быть не менее 6 символов"}), 400
    if get_user(email):
        return jsonify({"ok": False, "error": "Пользователь с таким email уже существует"}), 400
    create_user(email, password, name, surname, patronymic)
    confirm_token = create_confirm_token(email)
    send_confirm_email(email, confirm_token)
    return jsonify({
        "ok": True,
        "auto_confirmed": False,
        "message": "Письмо отправлено! Проверьте почту и подтвердите регистрацию."
    })


@app.route("/api/auth/login", methods=["POST"])
def login():
    body     = request.get_json(force=True)
    email    = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", "")).strip()
    if not email or not password:
        return jsonify({"ok": False, "error": "Введите email и пароль"}), 400
    user = get_user(email)
    if not user:
        return jsonify({"ok": False, "error": "Пользователь не зарегистрирован", "not_found": True}), 404
    if user["password"] != hash_password(password):
        return jsonify({"ok": False, "error": "Неверный email или пароль"}), 401
    if not user.get("confirmed"):
        return jsonify({"ok": False, "error": f"Email не подтверждён. Проверьте почту {email}.", "not_confirmed": True}), 403
    session_token = create_session(email)
    user_data = get_user_data(email)
    tariff = user_data.get("tariff", "demo")
    limits = TARIFF_LIMITS.get(tariff, TARIFF_LIMITS["demo"])
    full_name = " ".join(filter(None, [user.get("name",""), user.get("surname","")])).strip() or email.split("@")[0]
    return jsonify({
        "ok": True, "session_token": session_token, "email": email,
        "name": full_name, "tariff": tariff,
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
    full_name = " ".join(filter(None, [(user.get("name","") if user else ""), (user.get("surname","") if user else "")])).strip() or email.split("@")[0]
    return jsonify({
        "ok": True, "email": email, "name": full_name, "tariff": tariff,
        "queries_used": user_data.get("queries_used", 0),
        "queries_limit": limits["queries"],
        "generations_used": user_data.get("generations_used", 0),
        "generations_limit": limits["generations"],
    })


@app.route("/api/auth/confirm", methods=["POST"])
def confirm_email():
    body  = request.get_json(force=True)
    token = str(body.get("token", "")).strip()
    email, err = verify_confirm_token(token)
    if err:
        return jsonify({"ok": False, "error": err}), 401
    confirm_user(email)
    tokens = load_tokens()
    tokens[token]["used"] = True
    save_tokens(tokens)
    session_token = create_session(email)
    user_data = get_user_data(email)
    tariff = user_data.get("tariff", "demo")
    limits = TARIFF_LIMITS.get(tariff, TARIFF_LIMITS["demo"])
    user = get_user(email)
    full_name = " ".join(filter(None, [(user.get("name","") if user else ""), (user.get("surname","") if user else "")])).strip() or email.split("@")[0]
    return jsonify({
        "ok": True, "session_token": session_token, "email": email,
        "name": full_name, "tariff": tariff,
        "queries_used": user_data.get("queries_used", 0),
        "queries_limit": limits["queries"],
    })


@app.route("/api/auth/reset/request", methods=["POST"])
def reset_request():
    body  = request.get_json(force=True)
    email = str(body.get("email", "")).strip().lower()
    if not email or "@" not in email:
        return jsonify({"ok": False, "error": "Введите корректный email"}), 400
    if not get_user(email):
        return jsonify({"ok": False, "error": "Пользователь с таким email не найден"}), 404
    token = create_reset_token(email)
    send_reset_email(email, token)
    return jsonify({"ok": True, "message": f"Письмо отправлено на {email}"})


@app.route("/api/auth/reset/confirm", methods=["POST"])
def reset_confirm():
    body     = request.get_json(force=True)
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
# ПРОФИЛЬ
# ─────────────────────────────────────────────────────────────

@app.route("/api/auth/profile", methods=["POST"])
def update_profile():
    token = request.headers.get("X-Session-Token", "")
    email = verify_session(token)
    if not email:
        return jsonify({"ok": False, "error": "Не авторизован", "unauthorized": True}), 401
    body    = request.get_json(force=True)
    name    = str(body.get("name", "")).strip()
    surname = str(body.get("surname", "")).strip()
    users   = load_users()
    if email not in users:
        return jsonify({"ok": False, "error": "Пользователь не найден"}), 404
    users[email]["name"]    = name
    users[email]["surname"] = surname
    save_users(users)
    full_name = " ".join(filter(None, [name, surname])).strip() or email.split("@")[0]
    return jsonify({"ok": True, "name": full_name})


# ─────────────────────────────────────────────────────────────
# ЧАТ
# ─────────────────────────────────────────────────────────────

@app.route("/api/chat", methods=["POST"])
def chat():
    session_token = request.headers.get("X-Session-Token", "")
    email = verify_session(session_token)
    if not email:
        return jsonify({"ok": False, "error": "Не авторизован", "unauthorized": True}), 401
    body    = request.get_json(force=True)
    message = str(body.get("message", "")).strip()
    history = body.get("history", [])
    grade   = body.get("grade", None)
    if not message:
        return jsonify({"ok": False, "error": "Сообщение не может быть пустым"}), 400
    if len(message) > 12000:
        return jsonify({"ok": False, "error": "Сообщение слишком длинное"}), 400
    allowed, limit_msg = check_limit(email)
    if not allowed:
        return jsonify({"ok": False, "error": limit_msg, "limit_exceeded": True}), 403
    processed, clarification = preprocess_message(message, grade)
    if clarification:
        return jsonify({"ok": True, "reply": clarification, "clarification": True})
    messages = []
    for item in history[-10:]:
        role    = item.get("role")
        content = item.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": processed})
    try:
        response = client.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS,
            system=build_system_prompt(SYSTEM_PROMPT, grade), messages=messages
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
        "ok": True, "reply": reply,
        "queries_used": user_data.get("queries_used", 0),
        "queries_limit": TARIFF_LIMITS.get(tariff, TARIFF_LIMITS["demo"])["queries"],
        "tariff": tariff
    })


SYSTEM_PROMPT_PRES = """Ты генератор содержания слайдов для школьных презентаций по ФГОС.

КРИТИЧЕСКИ ВАЖНО: Отвечай ТОЛЬКО валидным JSON-массивом. Никакого другого текста до и после. Никаких markdown-блоков (```). Только чистый JSON.

Формат каждого слайда: {"title": "Заголовок слайда", "body": "• пункт 1\n• пункт 2\n• пункт 3"}

ОБЯЗАТЕЛЬНЫЕ ПРАВИЛА ФОРМАТИРОВАНИЯ СЛАЙДА:
- Буллеты начинаются с символа •
- Буллеты должны быть конкретными, с реальными терминами, фактами, примерами — не общими словами
- ОГРАНИЧЕНИЯ ВМЕСТИМОСТИ СЛАЙДА передаются в запросе как max_bullets и max_chars_per_bullet — СТРОГО соблюдай оба числа
- Один буллет = ОДНА строка, без переносов внутри буллета
- Заголовок слайда: не более 55 символов"""


@app.route("/api/generate", methods=["POST"])
def generate():
    session_token = request.headers.get("X-Session-Token", "")
    email = verify_session(session_token)
    if not email:
        return jsonify({"ok": False, "error": "Не авторизован", "unauthorized": True}), 401
    body     = request.get_json(force=True)
    message  = str(body.get("message", "")).strip()
    gen_type = str(body.get("type", "")).strip()
    grade    = body.get("grade", None)
    max_bullets        = int(body.get("max_bullets", 5))
    max_chars_per_bullet = int(body.get("max_chars_per_bullet", 70))
    if not message:
        return jsonify({"ok": False, "error": "Сообщение не может быть пустым"}), 400
    max_msg_len = 12000 if gen_type == "pres" else 6000
    if len(message) > max_msg_len:
        return jsonify({"ok": False, "error": "Сообщение слишком длинное"}), 400
    allowed, limit_msg = check_limit(email)
    if not allowed:
        return jsonify({"ok": False, "error": limit_msg, "limit_exceeded": True}), 403
    processed, clarification = preprocess_message(message, grade)
    if clarification:
        return jsonify({"ok": True, "reply": clarification, "clarification": True})
    if gen_type == "pres":
        # Формируем системный промпт с точными ограничениями вместимости слайда
        pres_system = build_system_prompt(SYSTEM_PROMPT_PRES, grade) + f"\n\nВМЕСТИМОСТЬ СЛАЙДА (рассчитано по размеру экрана):\n- Максимум буллетов на слайд: {max_bullets}\n- Максимум символов в одном буллете: {max_chars_per_bullet}\nЭТИ ЧИСЛА — физический предел слайда. Превышение = текст выйдет за границы. Строго соблюдай."
        try:
            response = client.messages.create(
                model=MODEL_GEN, max_tokens=MAX_TOKENS_GEN,
                system=pres_system,
                messages=[{"role": "user", "content": processed}]
            )
            raw = response.content[0].text.strip()
            raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
            raw = re.sub(r'\s*```\s*$', '', raw, flags=re.MULTILINE)
            slides = json.loads(raw)
            if not isinstance(slides, list) or len(slides) < 2:
                raise ValueError("invalid slides")
        except (json.JSONDecodeError, ValueError):
            return jsonify({"ok": False, "error": "Не удалось создать презентацию. Попробуйте ещё раз."}), 500
        except anthropic.APIError as e:
            return jsonify({"ok": False, "error": f"Ошибка Claude API: {str(e)}"}), 502
        except Exception as e:
            return jsonify({"ok": False, "error": f"Внутренняя ошибка: {str(e)}"}), 500
        increment_query(email)
        user_data = get_user_data(email)
        tariff = user_data.get("tariff", "demo")
        return jsonify({
            "ok": True, "slides": slides,
            "queries_used": user_data.get("queries_used", 0),
            "queries_limit": TARIFF_LIMITS.get(tariff, TARIFF_LIMITS["demo"])["queries"],
            "tariff": tariff
        })
    else:
        try:
            response = client.messages.create(
                model=MODEL_GEN, max_tokens=MAX_TOKENS_GEN,
                system=build_system_prompt(SYSTEM_PROMPT, grade),
                messages=[{"role": "user", "content": processed}]
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
            "ok": True, "reply": reply,
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
        "ok": True, "tariff": tariff,
        "queries_used": user_data.get("queries_used", 0),
        "queries_limit": limits["queries"],
        "generations_used": user_data.get("generations_used", 0),
        "generations_limit": limits["generations"]
    })


if __name__ == "__main__":
    print("ПроУрок Web API v3.3 — порт 5001")
    app.run(host="0.0.0.0", port=5001, debug=False)
