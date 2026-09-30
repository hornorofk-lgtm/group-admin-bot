import os
import re
import sqlite3
import logging
from datetime import datetime, date, timedelta

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ==========================================
# SETTINGS
# ==========================================

TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "bot.db")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ==========================================
# DATABASE
# ==========================================

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


# ==========================================
# USER REGISTER
# ==========================================

def register_user(user):

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


# ==========================================
# GROUP REGISTER
# ==========================================

def register_group(chat_id, title, owner_id=None):

    conn = get_db()

    conn.execute("""
        INSERT OR IGNORE INTO groups
        (chat_id, title, owner_id, created_at)
        VALUES (?, ?, ?, ?)
    """, (
        chat_id,
        title or "Unknown Group",
        owner_id,
        datetime.now().isoformat(),
    ))

    conn.execute("""
        UPDATE groups
        SET title = ?
        WHERE chat_id = ?
    """, (
        title or "Unknown Group",
        chat_id,
    ))

    conn.commit()
    conn.close()


# ==========================================
# CHECK GROUP ADMIN
# ==========================================

async def is_group_admin(
    context,
    chat_id,
    user_id
):

    try:

        member = await context.bot.get_chat_member(
            chat_id=chat_id,
            user_id=user_id,
        )

        return member.status in (
            "administrator",
            "creator",
        )

    except Exception as e:

        logger.error(
            "Admin check error: %s",
            e,
        )

        return False


# ==========================================
# FIND USER GROUPS
# ==========================================

async def get_user_groups(
    context,
    user_id
):

    conn = get_db()

    rows = conn.execute("""
        SELECT chat_id, title
        FROM groups
        ORDER BY title
    """).fetchall()

    conn.close()

    result = []

    for row in rows:

        if await is_group_admin(
            context,
            row["chat_id"],
            user_id,
        ):

            result.append(row)

    return result


# ==========================================
# START
# ==========================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    register_user(user)

    chat = update.effective_chat

    if chat.type in (
        "group",
        "supergroup",
    ):

        register_group(
            chat.id,
            chat.title,
        )

        group_name = chat.title or "This Group"

    else:

        group_name = "Luxury Nexus"

    keyboard = [
        [
            InlineKeyboardButton(
                "🗓️ Today Schedule",
                callback_data="today",
            ),
            InlineKeyboardButton(
                "📅 Tomorrow",
                callback_data="tomorrow",
            ),
        ],
        [
            InlineKeyboardButton(
                "➕ Add Schedule",
                callback_data="add",
            ),
            InlineKeyboardButton(
                "📋 My Schedule",
                callback_data="mine",
            ),
        ],
        [
            InlineKeyboardButton(
                "👑 Admins",
                callback_data="admins",
            ),
            InlineKeyboardButton(
                "📊 Reports",
                callback_data="reports",
            ),
        ],
        [
            InlineKeyboardButton(
                "ℹ️ Help",
                callback_data="help",
            ),
        ],
    ]

    text = (
        f"✨ **{group_name}** ✨\n\n"
        "🗓️ **Group Admin Management Bot**\n\n"
        f"Welcome to **{group_name}** 👋\n\n"
        "ဒီ Bot က Group Admin တွေရဲ့\n"
        "🗓️ Schedule\n"
        "⏰ Duty Time\n"
        "🔔 Reminder\n"
        "👥 Member Activity\n"
        "📊 Daily / Weekly Report\n"
        "တွေကို စီမံပေးပါတယ်။\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "👇 **အောက်က Button တွေကနေ ရွေးပါ**"
    )

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ==========================================
# SAVE SCHEDULE
# ==========================================

def save_schedule(
    chat_id,
    admin_id,
    schedule_date,
    start_time,
    end_time,
    shift,
    note=""
):

    conn = get_db()

    cur = conn.cursor()

    cur.execute("""
        INSERT INTO schedules
        (
            chat_id,
            admin_id,
            schedule_date,
            start_time,
            end_time,
            shift,
            note,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        chat_id,
        admin_id,
        schedule_date,
        start_time,
        end_time,
        shift,
        note,
        datetime.now().isoformat(),
    ))

    schedule_id = cur.lastrowid

    conn.commit()
    conn.close()

    return schedule_id


# ==========================================
# PARSE TIME
# ==========================================

def parse_time(text):

    text = text.strip()

    pattern = r"^(\d{1,2})(?::(\d{2}))?\s*-\s*(\d{1,2})(?::(\d{2}))?$"

    match = re.match(
        pattern,
        text,
    )

    if not match:
        return None

    sh = int(match.group(1))
    sm = int(match.group(2) or 0)

    eh = int(match.group(3))
    em = int(match.group(4) or 0)

    if not (
        0 <= sh <= 23
        and 0 <= eh <= 23
        and 0 <= sm <= 59
        and 0 <= em <= 59
    ):
        return None

    return (
        f"{sh:02d}:{sm:02d}",
        f"{eh:02d}:{em:02d}",
    )


# ==========================================
# TEXT SCHEDULE INPUT
# ==========================================

async def handle_schedule_text(
    update,
    context,
):

    if update.effective_chat.type != "private":
        return

    user = update.effective_user

    text = update.message.text.strip()

    pattern = (
        r"^(today|tomorrow)\s+"
        r"(.+?)\s+"
        r"(day|night)"
        r"(?:\s+(.+))?$"
    )

    match = re.match(
        pattern,
        text,
        re.IGNORECASE,
    )

    if not match:

        return

    day_word = match.group(1).lower()
    time_text = match.group(2)
    shift = match.group(3).lower()
    note = match.group(4) or ""

    parsed = parse_time(time_text)

    if not parsed:

        await update.message.reply_text(
            "❌ Time format မမှန်ပါဘူး။\n\n"
            "ဥပမာ:\n"
            "Today 12:00-15:00 day\n"
            "Tomorrow 20:00-00:00 night"
        )

        return

    start_time, end_time = parsed

    groups = await get_user_groups(
        context,
        user.id,
    )

    if not groups:

        await update.message.reply_text(
            "❌ မင်းက Bot မှာ အသုံးပြုနိုင်တဲ့ "
            "Group Admin မတွေ့ပါဘူး။\n\n"
            "Bot ကို Group ထဲမှာ Admin အဖြစ် "
            "ထည့်ထားပြီး ပြန်စမ်းပါ။"
        )

        return

    if len(groups) == 1:

        group = groups[0]

        target_chat_id = group["chat_id"]
        group_title = group["title"]

        if day_word == "today":
            target_date = date.today()
        else:
            target_date = date.today() + timedelta(days=1)

        save_schedule(
            target_chat_id,
            user.id,
            target_date.isoformat(),
            start_time,
            end_time,
            shift,
            note,
        )

        await update.message.reply_text(
            "✅ **Schedule Saved**\n\n"
            f"👑 Admin: {user.full_name}\n"
            f"🏠 Group: {group_title}\n"
            f"🗓️ Date: {day_word.title()}\n"
            f"⏰ Time: {start_time} - {end_time}\n"
            f"{'☀️ Day' if shift == 'day' else '🌙 Night'}\n"
            f"📝 Note: {note or '-'}",
            parse_mode="Markdown",
        )

        return

    context.user_data["pending_schedule"] = {
        "admin_id": user.id,
        "day": day_word,
        "start_time": start_time,
        "end_time": end_time,
        "shift": shift,
        "note": note,
    }

    buttons = []

    for group in groups:

        buttons.append([
            InlineKeyboardButton(
                group["title"][:50],
                callback_data=f"sg:{group['chat_id']}",
            )
        ])

    await update.message.reply_text(
        "🏠 **ဘယ် Group အတွက် Schedule မှတ်မလဲ?**",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ==========================================
# CALLBACKS
# ==========================================

async def callbacks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    data = query.data

    # ------------------------------
    # SELECT GROUP
    # ------------------------------

    if data.startswith("sg:"):

        chat_id = int(
            data.split(":", 1)[1]
        )

        pending = context.user_data.get(
            "pending_schedule"
        )

        if not pending:
            await query.edit_message_text(
                "❌ Schedule session expired."
            )
            return

        user_id = query.from_user.id

        admin = await is_group_admin(
            context,
            chat_id,
            user_id,
        )

        if not admin:

            await query.edit_message_text(
                "❌ မင်းက ဒီ Group ရဲ့ Admin "
                "မဟုတ်တော့ပါဘူး။"
            )

            return

        if pending["day"] == "today":
            target_date = date.today()
        else:
            target_date = (
                date.today()
                + timedelta(days=1)
            )

        save_schedule(
            chat_id,
            user_id,
            target_date.isoformat(),
            pending["start_time"],
            pending["end_time"],
            pending["shift"],
            pending["note"],
        )

        conn = get_db()

        row = conn.execute("""
            SELECT title
            FROM groups
            WHERE chat_id = ?
        """, (chat_id,)).fetchone()

        conn.close()

        title = (
            row["title"]
            if row
            else "Group"
        )

        await query.edit_message_text(
            "✅ **Schedule Saved**\n\n"
            f"🏠 Group: {title}\n"
            f"🗓️ {pending['day'].title()}\n"
            f"⏰ {pending['start_time']} - "
            f"{pending['end_time']}\n"
            f"{'☀️ Day' if pending['shift'] == 'day' else '🌙 Night'}\n"
            f"📝 {pending['note'] or '-'}",
            parse_mode="Markdown",
        )

        context.user_data.pop(
            "pending_schedule",
            None,
        )

        return

    # ------------------------------
    # HELP
    # ------------------------------

    if data == "help":

        await query.edit_message_text(
            "ℹ️ **Help**\n\n"
            "Schedule တင်ရန်:\n\n"
            "`Today 12:00-15:00 day`\n"
            "`Tomorrow 20:00-00:00 night`\n\n"
            "🗓️ Today / Tomorrow\n"
            "☀️ Day / 🌙 Night\n"
            "⏰ Start - End Time\n\n"
            "Bot က Telegram Group Admin "
            "ဟုတ်/မဟုတ် အလိုအလျောက်စစ်ပေးပါတယ်။",
            parse_mode="Markdown",
        )

        return

    # ------------------------------
    # TODAY
    # ------------------------------

    if data == "today":

        await show_schedule(
            query,
            context,
            date.today(),
        )

        return

    # ------------------------------
    # TOMORROW
    # ------------------------------

    if data == "tomorrow":

        await show_schedule(
            query,
            context,
            date.today()
            + timedelta(days=1),
        )

        return

    # ------------------------------
    # MY SCHEDULE
    # ------------------------------

    if data == "mine":

        await show_my_schedule(
            query,
            context,
        )

        return

    # ------------------------------
    # ADMINS
    # ------------------------------

    if data == "admins":

        await query.edit_message_text(
            "👑 **Admins**\n\n"
            "Admin status ကို Telegram Group "
            "ထဲကနေ အလိုအလျောက်စစ်ပါတယ်။\n\n"
            "Admin ဖြစ်/မဖြစ် စမ်းရန်:\n"
            "`/checkadmin`",
            parse_mode="Markdown",
        )

        return

    # ------------------------------
    # REPORTS
    # ------------------------------

    if data == "reports":

        await query.edit_message_text(
            "📊 **Reports**\n\n"
            "Daily / Weekly Report system "
            "ကို ဆက်လက်ထည့်သွင်းနိုင်ပါတယ်။"
        )

        return

    # ------------------------------
    # ADD
    # ------------------------------

    if data == "add":

        await query.edit_message_text(
            "➕ **Add Schedule**\n\n"
            "Bot ကို DM မှာ ဒီလိုပို့ပါ:\n\n"
            "`Today 12:00-15:00 day`\n\n"
            "သို့မဟုတ်\n\n"
            "`Tomorrow 20:00-00:00 night`",
            parse_mode="Markdown",
        )

        return


# ==========================================
# SHOW SCHEDULE
# ==========================================

async def show_schedule(
    query,
    context,
    target_date,
):

    user_id = query.from_user.id

    groups = await get_user_groups(
        context,
        user_id,
    )

    if not groups:

        await query.edit_message_text(
            "❌ Admin Group မတွေ့ပါဘူး။"
        )

        return

    chat_ids = [
        group["chat_id"]
        for group in groups
    ]

    placeholders = ",".join(
        "?" for _ in chat_ids
    )

    conn = get_db()

    rows = conn.execute(
        f"""
        SELECT
            s.*,
            g.title,
            u.full_name
        FROM schedules s
        LEFT JOIN groups g
            ON s.chat_id = g.chat_id
        LEFT JOIN users u
            ON s.admin_id = u.user_id
        WHERE s.chat_id IN ({placeholders})
        AND s.schedule_date = ?
        ORDER BY s.start_time
        """,
        (*chat_ids, target_date.isoformat()),
    ).fetchall()

    conn.close()

    if not rows:

        await query.edit_message_text(
            f"🗓️ **{target_date.isoformat()}**\n\n"
            "Schedule မရှိသေးပါဘူး။",
            parse_mode="Markdown",
        )

        return

    lines = [
        f"🗓️ **Schedule — {target_date.isoformat()}**\n"
    ]

    for row in rows:

        shift_icon = (
            "☀️"
            if row["shift"] == "day"
            else "🌙"
        )

        lines.append(
            f"{shift_icon} **{row['full_name']}**\n"
            f"🏠 {row['title']}\n"
            f"⏰ {row['start_time']} - "
            f"{row['end_time']}\n"
            f"📝 {row['note'] or '-'}\n"
        )

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="Markdown",
    )


# ==========================================
# MY SCHEDULE
# ==========================================

async def show_my_schedule(
    query,
    context,
):

    user_id = query.from_user.id

    conn = get_db()

    rows = conn.execute("""
        SELECT
            s.*,
            g.title
        FROM schedules s
        LEFT JOIN groups g
            ON s.chat_id = g.chat_id
        WHERE s.admin_id = ?
        AND s.schedule_date >= ?
        ORDER BY
            s.schedule_date,
            s.start_time
        LIMIT 20
    """, (
        user_id,
        date.today().isoformat(),
    )).fetchall()
    if __name__ == "__main__":
    main()
