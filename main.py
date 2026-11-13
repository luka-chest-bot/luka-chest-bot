import os
import sqlite3
from pathlib import Path

import requests
from flask import Flask, jsonify, request, send_from_directory


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "chests.db"
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "8103392209"))
WEBAPP_URL = os.getenv(
    "WEBAPP_URL",
    "https://luka-chest-bot.onrender.com",
).rstrip("/")

app = Flask(__name__, static_folder="static", static_url_path="")


def get_db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with get_db() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                chest_number INTEGER,
                prize TEXT,
                is_blocked INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS chests (
                number INTEGER PRIMARY KEY,
                image_path TEXT,
                prize TEXT NOT NULL DEFAULT ''
            )
            """
        )

        existing = connection.execute(
            "SELECT COUNT(*) AS count FROM chests"
        ).fetchone()["count"]

        if existing == 0:
            connection.executemany(
                "INSERT INTO chests (number, prize) VALUES (?, ?)",
                [(number, "") for number in range(1, 26)],
            )


def telegram_request(method, payload=None):
    if not BOT_TOKEN:
        return None

    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        json=payload or {},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def set_webhook():
    if not BOT_TOKEN:
        print("BOT_TOKEN is not configured")
        return

    webhook_url = f"{WEBAPP_URL}/telegram/webhook"

    try:
        result = telegram_request(
            "setWebhook",
            {
                "url": webhook_url,
                "allowed_updates": ["message"],
            },
        )
        print("Telegram webhook:", result)
    except requests.RequestException as error:
        print("Webhook setup failed:", error)


def send_start_message(chat_id, first_name=""):
    greeting = f"Привет, {first_name}!" if first_name else "Привет!"

    telegram_request(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": (
                f"{greeting}\n\n"
                "Открой игру и выбери один из 25 сундуков."
            ),
            "reply_markup": {
                "keyboard": [
                    [
                        {
                            "text": "🎁 Открыть сундуки",
                            "web_app": {"url": WEBAPP_URL},
                        }
                    ]
                ],
                "resize_keyboard": True,
            },
        },
    )


@app.get("/")
def index():
    index_file = Path(app.static_folder) / "index.html"

    if index_file.exists():
        return send_from_directory(app.static_folder, "index.html")

    return (
        "Сервер работает. Игровая страница будет добавлена следующим шагом.",
        200,
    )


@app.get("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "service": "luka-chest-bot",
        }
    )


@app.post("/telegram/webhook")
def telegram_webhook():
    update = request.get_json(silent=True) or {}
    message = update.get("message") or {}
    text = message.get("text", "")
    chat = message.get("chat") or {}
    user = message.get("from") or {}

    if chat.get("id") and text.startswith("/start"):
        send_start_message(
            chat_id=chat["id"],
            first_name=user.get("first_name", ""),
        )

    return jsonify({"ok": True})


@app.get("/api/chests")
def get_chests():
    with get_db() as connection:
        rows = connection.execute(
            """
            SELECT number, image_path, prize
            FROM chests
            ORDER BY number
            """
        ).fetchall()

    return jsonify(
        [
            {
                "number": row["number"],
                "image_path": row["image_path"],
                "prize": row["prize"],
            }
            for row in rows
        ]
    )


init_db()
set_webhook()
