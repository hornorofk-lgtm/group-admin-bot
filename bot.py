import os
import sqlite3
import logging
from datetime import datetime

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

# ==================================
# SETTINGS
# ==================================

TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "bot.db")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ==================================
# DATABASE
# ==================================

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            started_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            chat_id INTEGER PRIMARY KEY,
            title TEXT,
            owner_id INTEGER,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS group_admins (
            chat_id INTEGER,
            user_id INTEGER,
            role TEXT DEFAULT 'admin',
            PRIMARY KEY (chat_id, user_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            admin_id INTEGER,
            schedule_date TEXT,
            start_time TEXT,
            end_time TEXT,
            shift TEXT,
            note TEXT,
            created_at TEXT
        )
    """)

    conn.commit()
    conn.close()


# ==================================
# CHECK TELEGRAM ADMIN
# ==================================

async def is_group_admin(
    context,
    chat_id: int,
    user_id: int
) -> bool:

    try:

        member = await context.bot.get_chat_member(
            chat_id=chat_id,
            user_id=user_id
        )

        return member.status in (
            "administrator",
            "creator",
        )

    except Exception as e:

        logger.error(
            "Admin check failed: %s",
            e
        )

        return False


# ==================================
# START
# ==================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:
        return

    conn = get_db()

    conn.execute("""
        INSERT OR REPLACE INTO users
        (user_id, username, full_name, started_at)
        VALUES (?, ?, ?, ?)
    """, (
        user.id,
        user.username or "",
        user.full_name or "",
        datetime.now().isoformat(),
    ))

    conn.commit()
    conn.close()

    await update.message.reply_text(
        "✨ LUXURY NEXUS ✨\n\n"
        "🗓️ Group Admin Management Bot\n\n"
        "Welcome 👋\n\n"
        "ဒီ Bot က Telegram Group Admin "
        "ဟုတ်/မဟုတ်ကို အလိုအလျောက်စစ်ပေးနိုင်ပါတယ်။"
    )


# ==================================
# MY ID
# ==================================

async def myid(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        f"🆔 Your Telegram ID:\n\n"
        f"`{update.effective_user.id}`",
        parse_mode="Markdown",
    )


# ==================================
# GROUP ID
# ==================================

async def groupid(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    chat = update.effective_chat

    if chat.type not in (
        "group",
        "supergroup"
    ):

        await update.message.reply_text(
            "ဒီ command ကို Group ထဲမှာသုံးပါ။"
        )

        return

    await update.message.reply_text(
        f"🆔 Group ID:\n\n"
        f"`{chat.id}`\n\n"
        f"📌 Group Name:\n"
        f"{chat.title}",
        parse_mode="Markdown",
    )


# ==================================
# TEST ADMIN
# ==================================

async def checkadmin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    chat = update.effective_chat
    user = update.effective_user

    if chat.type not in (
        "group",
        "supergroup"
    ):

        await update.message.reply_text(
            "ဒီ command ကို Group ထဲမှာသုံးပါ။"
        )

        return

    admin = await is_group_admin(
        context,
        chat.id,
        user.id
    )

    if admin:

        await update.message.reply_text(
            "✅ မင်းက ဒီ Group ရဲ့ Admin ဖြစ်ပါတယ်။"
        )

    else:

        await update.message.reply_text(
            "❌ မင်းက ဒီ Group ရဲ့ Admin မဟုတ်ပါဘူး။"
        )


# ==================================
# ERROR
# ==================================

async def error_handler(
    update,
    context
):

    logger.error(
        "Update error: %s",
        context.error
    )


# ==================================
# MAIN
# ==================================

def main():

    if not TOKEN:

        raise RuntimeError(
            "BOT_TOKEN is missing."
        )

    init_db()

    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "myid",
            myid
        )
    )

    app.add_handler(
        CommandHandler(
            "groupid",
            groupid
        )
    )

    app.add_handler(
        CommandHandler(
            "checkadmin",
            checkadmin
        )
    )

    app.add_error_handler(
        error_handler
    )

    print(
        "🤖 Group Admin Management Bot is running..."
    )

    app.run_polling()


if __name__ == "__main__":
    main()
