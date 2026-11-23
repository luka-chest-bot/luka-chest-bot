import os
import sqlite3
import json
import random
from pathlib import Path
import requests
from flask import Flask, jsonify, request, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "chests.db"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "8103392209"))
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://luka-chest-bot.onrender.com").rstrip("/")

app = Flask(__name__, static_folder="static", static_url_path="")

# ---------- База данных ----------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        # Пользователи
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                is_blocked INTEGER NOT NULL DEFAULT 0,
                has_opened INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Индивидуальные сундуки для каждого пользователя
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_chests (
                telegram_id INTEGER,
                chest_number INTEGER,
                prize TEXT NOT NULL,
                is_opened INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (telegram_id, chest_number)
            )
        """)
        conn.commit()

def generate_chests_for_user(telegram_id):
    """Создаёт 25 сундуков с призами по распределению."""
    prizes = (
        ["скидка 5 рублей"] * 2 +
        ["скидка 3 рубля"] * 5 +
        ["скидка 2 рубля"] * 15 +
        ["бесплатная банка"] * 1 +
        ["бесплатный картридж"] * 1 +
        ["бесплатная банка и картридж"]  * 1
    )
    random.shuffle(prizes)
    with get_db() as conn:
        # Проверяем, есть ли уже сундуки у пользователя
        existing = conn.execute(
            "SELECT COUNT(*) as cnt FROM user_chests WHERE telegram_id = ?",
            (telegram_id,)
        ).fetchone()["cnt"]
        if existing == 0:
            for i, prize in enumerate(prizes, start=1):
                conn.execute(
                    "INSERT INTO user_chests (telegram_id, chest_number, prize) VALUES (?, ?, ?)",
                    (telegram_id, i, prize)
                )
            conn.commit()

# ---------- Telegram API ----------
def telegram_request(method, payload=None):
    if not BOT_TOKEN:
        return None
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
            json=payload or {},
            timeout=15
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"Telegram error: {e}")
        return None

def set_webhook():
    if not BOT_TOKEN:
        print("BOT_TOKEN не задан")
        return
    url = f"{WEBAPP_URL}/telegram/webhook"
    telegram_request("setWebhook", {"url": url, "allowed_updates": ["message"]})

def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    telegram_request("sendMessage", payload)

# ---------- Эндпоинты ----------
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")

@app.get("/health")
def health():
    return jsonify({"status": "ok"})

@app.get("/api/chests")
def get_chests():
    """Возвращает сундуки для текущего пользователя (по initData)."""
    init_data = request.args.get("initData", "")
    print(f"--- DEBUG LOG: Получен initData: {init_data[:50]}... ---") # <--- ДОБАВИТЬ ЭТО
    telegram_id = None
    if init_data:
        # В реальном проекте здесь нужно проверять подпись initData,
        # но для простоты извлекаем user_id из JSON.
        try:
            from urllib.parse import parse_qs
            parsed = parse_qs(init_data)
            user_json = parsed.get("user", ["{}"])[0]
            user = json.loads(user_json)
            telegram_id = user.get("id")
        except Exception:
            pass
    if not telegram_id:
        return jsonify({"error": "Не удалось определить пользователя"}), 401

    # Проверка блокировки
    with get_db() as conn:
        user = conn.execute(
            "SELECT is_blocked, has_opened FROM users WHERE telegram_id = ?",
            (telegram_id,)
        ).fetchone()
    if user and user["is_blocked"]:
        return jsonify({"error": "Вы заблокированы"}), 403

    generate_chests_for_user(telegram_id)

    with get_db() as conn:
        rows = conn.execute(
            "SELECT chest_number, prize, is_opened FROM user_chests WHERE telegram_id = ? ORDER BY chest_number",
            (telegram_id,)
        ).fetchall()

    chests = []
    for row in rows:
        chests.append({
            "number": row["chest_number"],
            "is_opened": bool(row["is_opened"]),
            # Приз показываем только если сундук открыт
            "prize": row["prize"] if row["is_opened"] else None
        })
    return jsonify(chests)

@app.post("/api/open_chest")
def open_chest():
    """Открывает выбранный сундук, если пользователь ещё не открывал."""
    data = request.get_json(silent=True) or {}
    init_data = data.get("initData", "")
    chest_number = data.get("chest_number")

    telegram_id = None
    username = "нет_юзернейма"
    first_name = "Пользователь"

    # Извлекаем telegram_id, username и имя
    try:
        from urllib.parse import parse_qs
        parsed = parse_qs(init_data)
        user_json = parsed.get("user", ["{}"])[0]
        user_data = json.loads(user_json)
        telegram_id = user_data.get("id")
        username = user_data.get("username", "нет_юзернейма")
        first_name = user_data.get("first_name", "Пользователь")
    except Exception:
        pass

    if not telegram_id or not chest_number:
        return jsonify({"error": "Неверные данные"}), 400

    with get_db() as conn:
        user = conn.execute(
            "SELECT is_blocked, has_opened FROM users WHERE telegram_id = ?",
            (telegram_id,)
        ).fetchone()
    if user and user["is_blocked"]:
        return jsonify({"error": "Вы заблокированы"}), 403

    with get_db() as conn:
        already = conn.execute(
            "SELECT chest_number FROM user_chests WHERE telegram_id = ? AND is_opened = 1",
            (telegram_id,)
        ).fetchone()
        if already:
            return jsonify({"error": "Вы уже открыли сундук"}), 400

        conn.execute(
            "UPDATE user_chests SET is_opened = 1 WHERE telegram_id = ? AND chest_number = ?",
            (telegram_id, chest_number)
        )
        conn.execute(
            "INSERT INTO users (telegram_id, has_opened) VALUES (?, 1) ON CONFLICT(telegram_id) DO UPDATE SET has_opened = 1",
            (telegram_id,)
        )
        conn.commit()

        row = conn.execute(
            "SELECT prize FROM user_chests WHERE telegram_id = ? AND chest_number = ?",
            (telegram_id, chest_number)
        ).fetchone()
        prize = row["prize"] if row else "неизвестный приз"

    # Уведомление админу с юзернеймом
    if ADMIN_ID:
        send_message(
            ADMIN_ID,
            f"🎁 {first_name} (@{username}, ID: {telegram_id}) открыл сундук №{chest_number} и выиграл: {prize}"
        )

    return jsonify({"prize": prize})

# ---------- Админ-команды в Telegram ----------
@app.post("/telegram/webhook")
def telegram_webhook():
    update = request.get_json(silent=True) or {}
    message = update.get("message") or {}
    text = message.get("text", "")
    chat = message.get("chat") or {}
    user = message.get("from") or {}

    if not chat.get("id"):
        return jsonify({"ok": True})

    chat_id = chat["id"]
    telegram_id = user.get("id")

    # Команда /start
    if text == "/start":
        send_message(
            chat_id,
            f"Привет, {user.get('first_name', '')}!\n\nОткрой игру и выбери один из 25 сундуков.",
            reply_markup={
                "keyboard": [[{"text": "🎁 Открыть сундуки", "web_app": {"url": WEBAPP_URL}}]],
                "resize_keyboard": True
            }
        )
        return jsonify({"ok": True})

    # Админ-команды (только для ADMIN_ID)
    if telegram_id != ADMIN_ID:
        return jsonify({"ok": True})

    if text.startswith("/block"):
        parts = text.split()
        if len(parts) == 2:
            try:
                target = int(parts[1])
                with get_db() as conn:
                    conn.execute(
                        "INSERT INTO users (telegram_id, is_blocked) VALUES (?, 1) ON CONFLICT(telegram_id) DO UPDATE SET is_blocked = 1",
                        (target,)
                    )
                    conn.commit()
                send_message(chat_id, f"Пользователь {target} заблокирован.")
            except ValueError:
                send_message(chat_id, "Неверный ID.")
        else:
            send_message(chat_id, "Использование: /block <telegram_id>")

    elif text.startswith("/unblock"):
        parts = text.split()
        if len(parts) == 2:
            try:
                target = int(parts[1])
                with get_db() as conn:
                    conn.execute(
                        "UPDATE users SET is_blocked = 0 WHERE telegram_id = ?",
                        (target,)
                    )
                    conn.commit()
                send_message(chat_id, f"Пользователь {target} разблокирован.")
            except ValueError:
                send_message(chat_id, "Неверный ID.")
        else:
            send_message(chat_id, "Использование: /unblock <telegram_id>")

    elif text == "/stats":
        with get_db() as conn:
            rows = conn.execute(
                "SELECT telegram_id, chest_number, prize FROM user_chests WHERE is_opened = 1 ORDER BY telegram_id"
            ).fetchall()
        if not rows:
            send_message(chat_id, "Пока никто не открыл сундук.")
        else:
            lines = ["📊 Открытые сундуки:"]
            for r in rows:
                lines.append(f"ID {r['telegram_id']} → сундук {r['chest_number']}: {r['prize']}")
            send_message(chat_id, "\n".join(lines))

    return jsonify({"ok": True})

init_db()
set_webhook()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))