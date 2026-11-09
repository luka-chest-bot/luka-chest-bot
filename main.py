import os
import random
import sqlite3
import logging
from collections import Counter
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = 8103392209
DB_PATH = os.environ.get("DB_PATH", "chests.db")

# Замените названия на реальные призы.
# Каждый элемент — один приз; всего их должно быть 25.
PRIZES = [
    "скидка 5 рублей", "скидка 5 рублей", "скидка 2 рубля", "скидка 5 рублей", "скидка 3 рубля",
    "скидка 3 рубля", "скидка 3 рубля", "скидка 3 рубля", "скидка 3 рубля", "скидка 2 рубля",
    "скидка 2 рубля", "скидка 2 рубля", "скидка 2 рубля", "скидка 2 рубля", "скидка 2 рубля",
    "скидка 2 рубля", "скидка 2 рубля", "скидка 2 рубля", "скидка 2 рубля", "скидка 2 рубля",
    "скидка 3 рубля", "бесплатная банка", "скидка 3 рубля", "бесплатный картридж", "бесплатная банка и картридж"
] 

PRIZE_COUNTS = Counter(PRIZES)


def connect_db():
    return sqlite3.connect(DB_PATH, timeout=30)


def init_db():
    with connect_db() as db:
    db.execute("""
        CREATE TABLE IF NOT EXISTS claims (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            chest_id INTEGER,
            prize TEXT
        )
    """)
    db.commit()

def make_keyboard():
    with connect_db() as db:
        opened = {
            row[0] for row in db.execute("SELECT chest_id FROM claims").fetchall()
        }

    buttons = []
    for chest_id in range(1, 26):
        if chest_id in opened:
            text = "🔒"
            callback_data = "taken"
        else:
            text = f"🎁 {chest_id}"
            callback_data = f"open:{chest_id}"

        buttons.append(
            InlineKeyboardButton(text, callback_data=callback_data)
        )

    return InlineKeyboardMarkup([
        buttons[i:i + 5] for i in range(0, 25, 5)
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with connect_db() as db:
        existing = db.execute(
            "SELECT chest_id, prize FROM claims WHERE user_id = ?",
            (update.effective_user.id,),
        ).fetchone()

    if existing:
        await update.message.reply_text(
            f"Вы уже открыли сундук №{existing[0]}. Ваш приз: {existing[1]}"
        )
        return

    await update.message.reply_text(
        "Выберите один свободный сундук. Открыть можно только один раз:",
        reply_markup=make_keyboard(),
    )


async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "taken":
        await query.answer("Этот сундук уже открыт.", show_alert=True)
        return

    if not query.data.startswith("open:"):
        return

    chest_id = int(query.data.split(":")[1])
    user = query.from_user
    username = f"@{user.username}" if user.username else user.full_name

    try:
        with connect_db() as db:
            # Защищает от одновременных нажатий на один сундук.
            db.execute("BEGIN IMMEDIATE")

            already_opened = db.execute(
                "SELECT chest_id, prize FROM claims WHERE user_id = ?",
                (user.id,),
            ).fetchone()

            if already_opened:
                db.rollback()
                await query.edit_message_text(
                    f"Вы уже открыли сундук №{already_opened[0]}. "
                    f"Ваш приз: {already_opened[1]}"
                )
                return

            chest_taken = db.execute(
                "SELECT 1 FROM claims WHERE chest_id = ?",
                (chest_id,),
            ).fetchone()

                        if chest_taken:
                db.rollback()
                await query.edit_message_text(
                    "Этот сундук уже открыли. Выберите другой:",
                    reply_markup=make_keyboard(),
                )
                return

            used_prizes = Counter(
                row[0] for row in db.execute("SELECT prize FROM claims").fetchall()
            )
            
            available_prizes = [
              p for p in PRIZE_COUNTS.elements()  # безопасный обход с учетом количества
              if used_prizes[p] < PRIZE_COUNTS[p]
            ]

            if not available_prizes:
                db.rollback()
                await query.edit_message_text("Все призы уже разобраны.")
                return

            prize = random.choice(available_prizes)
            db.execute(
                "INSERT INTO claims (user_id, username, chest_id, prize) "
                "VALUES (?, ?, ?, ?)",
                (user.id, username, chest_id, prize),
            )
            db.commit()

    except sqlite3.IntegrityError:
        await query.edit_message_text(
            "Не удалось открыть этот сундук. Нажмите /start и выберите свободный."
        )
        return

    await query.edit_message_text(
        f"🎉 Вы открыли сундук №{chest_id}!\nВаш приз: {prize}"
    )

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"Открыт сундук №{chest_id}\n"
                f"Участник: {username}\n"
                f"Telegram ID: {user.id}\n"
                f"Приз: {prize}"
            ),
        )
    except Exception:
        logging.exception("Не получилось отправить уведомление администратору")

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot is running!")

    def log_message(self, format, *args):
        pass

def start_web_server():
    port = int(os.environ.get("PORT", 10000))

    server = ThreadingHTTPServer(
        ("0.0.0.0", port),
        HealthHandler,
    )

    logging.info("HTTP server started on port %s", port)

    threading.Thread(
        target=server.serve_forever,
        daemon=True,
    ).start()

def main():
    init_db()
    start_web_server()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_click))

    logging.info("Telegram bot is starting...")
    app.run_polling()

if __name__ == "__main__":
    main()
