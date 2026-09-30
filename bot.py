import os
import re
import sqlite3
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ChatType,
)
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

DB_PATH = "bot.db"
TZ = ZoneInfo("Asia/Yangon")

REMINDER_MINUTES = 15

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

current_application = None


# =========================================================
# DATABASE
# =========================================================

def db():
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            full_name TEXT,
            username TEXT,
            started_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            chat_id INTEGER PRIMARY KEY,
            title TEXT,
            added_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            user_id INTEGER,
            date TEXT,
            start_time TEXT,
            end_time TEXT,
            shift TEXT,
            started INTEGER DEFAULT 0,
            ended INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS group_admins (
            chat_id INTEGER,
            user_id INTEGER,
            added_at TEXT,
            PRIMARY KEY(chat_id, user_id)
        )
    """)

    conn.commit()
    conn.close()


# =========================================================
# SAVE USER
# =========================================================

def save_user(user):
    if not user:
        return

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO users
        (user_id, full_name, username, started_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET
            full_name=excluded.full_name,
            username=excluded.username
    """, (
        user.id,
        user.full_name,
        user.username or "",
        datetime.now(TZ).isoformat(),
    ))

    conn.commit()
    conn.close()


# =========================================================
# SAVE GROUP
# =========================================================

def save_group(chat):
    if not chat:
        return

    if chat.type not in (
        ChatType.GROUP,
        ChatType.SUPERGROUP,
    ):
        return

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO groups
        (chat_id, title, added_at)
        VALUES (?, ?, ?)
        ON CONFLICT(chat_id)
        DO UPDATE SET title=excluded.title
    """, (
        chat.id,
        chat.title or "Unknown Group",
        datetime.now(TZ).isoformat(),
    ))

    conn.commit()
    conn.close()


# =========================================================
# PREMIUM MENU
# =========================================================

def get_menu():
    bot_username = ""

    if current_application:
        try:
            bot_username = (
                current_application.bot.username
                or ""
            )
        except Exception:
            pass

    buttons = [
        [
            InlineKeyboardButton(
                "☀️ TODAY SCHEDULE",
                callback_data="today",
            ),
            InlineKeyboardButton(
                "🌙 TOMORROW",
                callback_data="tomorrow",
            ),
        ],
        [
            InlineKeyboardButton(
                "＋ ADD SCHEDULE",
                callback_data="add_schedule",
            ),
            InlineKeyboardButton(
                "📋 MY SCHEDULE",
                callback_data="myschedule",
            ),
        ],
        [
            InlineKeyboardButton(
                "👑 ADMINS",
                callback_data="admins",
            ),
            InlineKeyboardButton(
                "📊 REPORTS",
                callback_data="report",
            ),
        ],
        [
            InlineKeyboardButton(
                "＋ ADD GROUP",
                url=f"https://t.me/{bot_username}?startgroup=true"
                if bot_username else "https://t.me",
            ),
            InlineKeyboardButton(
                "＋ ADD CHANNEL",
                url=f"https://t.me/{bot_username}?startchannel=true"
                if bot_username else "https://t.me",
            ),
        ],
        [
            InlineKeyboardButton(
                "ℹ️ HELP",
                callback_data="help",
            )
        ],
    ]

    return InlineKeyboardMarkup(buttons)


# =========================================================
# WELCOME
# =========================================================

WELCOME_TEXT = """
╔════════════════════════════╗
        ✨ <b>LUXURY NEXUS</b> ✨
╚════════════════════════════╝

<b>🗓 GROUP ADMIN MANAGEMENT BOT</b>

Welcome to <b>Luxury Nexus</b> 👋

ဒီ Bot က Group Admin တွေရဲ့

🗓 <b>Schedule</b>
⏰ <b>Duty Time</b>
🔔 <b>Reminder</b>
👥 <b>Member Activity</b>
📊 <b>Daily / Weekly Report</b>

တွေကို စီမံပေးပါတယ်။

━━━━━━━━━━━━━━━━━━━━

👑 <b>ADMIN SCHEDULE</b>

Today / Tomorrow ကိုရွေးပြီး
☀️ Day / 🌙 Night
အချိန်တင်နိုင်ပါတယ်။

⏰ Schedule အချိန်ရောက်ရင်
Admin ကို အလိုအလျောက် <b>Mention</b> လုပ်ပြီး
Reminder ပေးပါမယ်။

━━━━━━━━━━━━━━━━━━━━

👇 <b>အောက်က Button တွေကနေ ရွေးပါ</b>
"""


# =========================================================
# ADMIN CHECK
# =========================================================

async def is_admin(update: Update):
    user = update.effective_user
    chat = update.effective_chat

    if not user or not chat:
        return False

    if user.id == OWNER_ID:
        return True

    if chat.type not in (
        ChatType.GROUP,
        ChatType.SUPERGROUP,
    ):
        return False

    try:
        member = await update.get_bot().get_chat_member(
            chat.id,
            user.id,
        )

        return member.status in (
            "administrator",
            "creator",
        )

    except Exception as e:
        logger.error("Admin check error: %s", e)
        return False


# =========================================================
# TIME PARSER
# =========================================================

def parse_time(value, shift):
    value = value.strip().lower()

    match = re.match(
        r"^(\d{1,2})(?::(\d{2}))?$",
        value,
    )

    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2) or 0)

    if hour < 1 or hour > 12:
        return None

    if minute < 0 or minute > 59:
        return None

    if shift == "day":
        if hour < 8:
            hour += 12

    elif shift == "night":
        if hour < 12:
            hour += 12

    if hour >= 24:
        hour -= 24

    return hour, minute


# =========================================================
# SCHEDULE TEXT PARSER
# =========================================================

def parse_schedule_text(text):
    pattern = re.compile(
        r"^(today|tomorrow)\s+"
        r"(\d{1,2}(?::\d{2})?)\s*-\s*"
        r"(\d{1,2}(?::\d{2})?)\s+"
        r"(day|night)$",
        re.IGNORECASE,
    )

    match = pattern.match(text.strip())

    if not match:
        return None

    day_name = match.group(1).lower()
    start_raw = match.group(2)
    end_raw = match.group(3)
    shift = match.group(4).lower()

    start = parse_time(start_raw, shift)
    end = parse_time(end_raw, shift)

    if not start or not end:
        return None

    today = datetime.now(TZ).date()

    if day_name == "today":
        target_date = today
    else:
        target_date = today + timedelta(days=1)

    return {
        "date": target_date,
        "start": start,
        "end": end,
        "shift": shift,
    }


# =========================================================
# SCHEDULE JOBS
# =========================================================

async def schedule_reminder(
    context: ContextTypes.DEFAULT_TYPE,
):
    data = context.job.data
    await send_notification(
        context,
        data["schedule_id"],
        "reminder",
    )


async def schedule_start(
    context: ContextTypes.DEFAULT_TYPE,
):
    data = context.job.data
    await send_notification(
        context,
        data["schedule_id"],
        "start",
    )


async def schedule_end(
    context: ContextTypes.DEFAULT_TYPE,
):
    data = context.job.data
    await send_notification(
        context,
        data["schedule_id"],
        "end",
    )


# =========================================================
# SCHEDULE NOTIFICATION
# =========================================================

async def send_notification(
    context,
    schedule_id,
    notification_type,
):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            s.id,
            s.chat_id,
            s.user_id,
            s.date,
            s.start_time,
            s.end_time,
            s.shift,
            u.full_name
        FROM schedules s
        LEFT JOIN users u
        ON s.user_id = u.user_id
        WHERE s.id = ?
    """, (schedule_id,))

    row = cur.fetchone()
    conn.close()

    if not row:
        return

    name = row[7] or str(row[2])

    # =====================================================
    # REAL TELEGRAM MENTION
    # =====================================================

    safe_name = (
        name
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )

    mention = (
        f'<a href="tg://user?id={row[2]}">'
        f'{safe_name}</a>'
    )

    start_time = row[4]
    end_time = row[5]
    shift = row[6].upper()

    if notification_type == "reminder":

        title = "⏰ SHIFT REMINDER"

        message = (
            f"Admin {mention}\n\n"
            f"Your shift starts in "
            f"<b>{REMINDER_MINUTES} minutes</b>."
        )

    elif notification_type == "start":

        title = "🔔 SHIFT STARTED"

        message = (
            f"Admin {mention}\n\n"
            "Your scheduled shift has started."
        )

    else:

        title = "🏁 SHIFT ENDED"

        message = (
            f"Admin {mention}\n\n"
            "Your scheduled shift has ended."
        )

    text = f"""
╔══ <b>{title}</b> ══╗

{message}

━━━━━━━━━━━━━━━━━━━━

🕐 <b>{start_time} – {end_time}</b>
🌙 <b>{shift}</b>
"""

    try:
        await context.bot.send_message(
            chat_id=row[1],
            text=text,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(
            "Notification error: %s",
            e,
        )


# =========================================================
# CREATE JOBS
# =========================================================

def create_schedule_jobs(
    application,
    schedule_id,
    chat_id,
    start_dt,
    end_dt,
):
    now = datetime.now(TZ)

    reminder_dt = (
        start_dt -
        timedelta(minutes=REMINDER_MINUTES)
    )

    if reminder_dt > now:
        application.job_queue.run_once(
            schedule_reminder,
            when=reminder_dt,
            data={
                "schedule_id": schedule_id,
            },
            name=f"reminder_{schedule_id}",
        )

    if start_dt > now:
        application.job_queue.run_once(
            schedule_start,
            when=start_dt,
            data={
                "schedule_id": schedule_id,
            },
            name=f"start_{schedule_id}",
        )

    if end_dt > now:
        application.job_queue.run_once(
            schedule_end,
            when=end_dt,
            data={
                "schedule_id": schedule_id,
            },
            name=f"end_{schedule_id}",
        )


# =========================================================
# RESTORE JOBS
# =========================================================

async def restore_jobs(
    application,
):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            chat_id,
            user_id,
            date,
            start_time,
            end_time
        FROM schedules
    """)

    rows = cur.fetchall()
    conn.close()

    now = datetime.now(TZ)

    for row in rows:

        schedule_id = row[0]

        try:
            date_obj = datetime.strptime(
                row[3],
                "%Y-%m-%d",
            ).date()

            start_h, start_m = map(
                int,
                row[4].split(":"),
            )

            end_h, end_m = map(
                int,
                row[5].split(":"),
            )

            start_dt = datetime(
                date_obj.year,
                date_obj.month,
                date_obj.day,
                start_h,
                start_m,
                tzinfo=TZ,
            )

            end_dt = datetime(
                date_obj.year,
                date_obj.month,
                date_obj.day,
                end_h,
                end_m,
                tzinfo=TZ,
            )

            if end_dt < start_dt:
                end_dt += timedelta(days=1)

            if end_dt > now:
                create_schedule_jobs(
                    application,
                    schedule_id,
                    row[1],
                    start_dt,
                    end_dt,
                )

        except Exception as e:
            logger.error(
                "Restore job error: %s",
                e,
            )


# =========================================================
# ADD SCHEDULE CORE
# =========================================================

async def add_schedule_from_text(
    update,
    context,
    schedule_text,
):
    user = update.effective_user
    chat = update.effective_chat
    message = update.effective_message

    if not user or not chat or not message:
        return

    save_user(user)
    save_group(chat)

    parsed = parse_schedule_text(
        schedule_text
    )

    if not parsed:
        await message.reply_text(
            """
❌ <b>Schedule Format မမှန်ပါ</b>

ဥပမာ -

<code>Today 1:30-3:00 day</code>

သို့မဟုတ်

<code>Tomorrow 8:00-12:00 night</code>
""",
            parse_mode="HTML",
        )
        return

    target_date = parsed["date"]
    start_h, start_m = parsed["start"]
    end_h, end_m = parsed["end"]
    shift = parsed["shift"]

    start_dt = datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        start_h,
        start_m,
        tzinfo=TZ,
    )

    end_dt = datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        end_h,
        end_m,
        tzinfo=TZ,
    )

    if end_dt <= start_dt:
        end_dt += timedelta(days=1)

    now = datetime.now(TZ)

    if start_dt <= now:
        await message.reply_text(
            "❌ Schedule Start Time က လက်ရှိအချိန်ထက် နောက်ကျရပါမယ်။"
        )
        return

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO schedules
        (
            chat_id,
            user_id,
            date,
            start_time,
            end_time,
            shift,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        chat.id,
        user.id,
        target_date.isoformat(),
        start_dt.strftime("%H:%M"),
        end_dt.strftime("%H:%M"),
        shift,
        now.isoformat(),
    ))

    schedule_id = cur.lastrowid

    conn.commit()
    conn.close()

    create_schedule_jobs(
        context.application,
        schedule_id,
        chat.id,
        start_dt,
        end_dt,
    )

    await message.reply_text(
        f"""
╔══ ✨ <b>SCHEDULE ADDED</b> ══╗

👤 Admin: <b>{user.full_name}</b>

📅 <b>{target_date.strftime("%d %B %Y")}</b>
🕐 <b>{start_dt.strftime("%I:%M %p")} – {end_dt.strftime("%I:%M %p")}</b>
🌙 <b>{shift.upper()}</b>

⏰ Reminder: <b>{REMINDER_MINUTES} minutes before</b>

🔔 Start notification
🏁 End notification

━━━━━━━━━━━━━━━━━━━━
✅ Schedule saved successfully.
""",
        parse_mode="HTML",
    )


# =========================================================
# RAW TEXT SCHEDULE
# =========================================================

async def schedule_text_handler(
    update,
    context,
):
    message = update.effective_message

    if not message or not message.text:
        return

    text = message.text.strip()

    if text.startswith("/"):
        return

    if update.effective_chat.type not in (
        ChatType.GROUP,
        ChatType.SUPERGROUP,
    ):
        return

    if not await is_admin(update):
        return

    if not re.match(
        r"^(today|tomorrow)\b",
        text,
        re.IGNORECASE,
    ):
        return

    await add_schedule_from_text(
        update,
        context,
        text,
    )


# =========================================================
# /START
# =========================================================

async def start_command(
    update,
    context,
):
    user = update.effective_user
    chat = update.effective_chat

    save_user(user)

    if chat:
        save_group(chat)

    await update.effective_message.reply_text(
        WELCOME_TEXT,
        parse_mode="HTML",
        reply_markup=get_menu(),
    )


# =========================================================
# SHOW DAY
# =========================================================

async def show_day(
    update,
    day,
):
    chat = update.effective_chat

    if not chat:
        return

    today = datetime.now(TZ).date()

    if day == "today":
        target = today
        title = "☀️ TODAY SCHEDULE"
    else:
        target = today + timedelta(days=1)
        title = "🌙 TOMORROW SCHEDULE"

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            s.start_time,
            s.end_time,
            s.shift,
            u.full_name,
            s.user_id
        FROM schedules s
        LEFT JOIN users u
        ON s.user_id = u.user_id
        WHERE s.chat_id = ?
        AND s.date = ?
        ORDER BY s.start_time
    """, (
        chat.id,
        target.isoformat(),
    ))

    rows = cur.fetchall()
    conn.close()

    if not rows:
        text = f"""
╔══ <b>{title}</b> ══╗

📅 <b>{target.strftime("%d %B %Y")}</b>

No schedules found.
"""

    else:
        lines = [
            f"╔══ <b>{title}</b> ══╗",
            "",
            f"📅 <b>{target.strftime('%d %B %Y')}</b>",
            "",
        ]

        for row in rows:

            name = row[3] or str(row[4])

            safe_name = (
                name
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )

            mention = (
                f'<a href="tg://user?id={row[4]}">'
                f'{safe_name}</a>'
            )

            lines.append(
                f"👤 {mention}\n"
                f"🕐 <b>{row[0]} – {row[1]}</b>\n"
                f"🌙 {row[2].upper()}\n"
                f"━━━━━━━━━━━━━━━━"
            )

        text = "\n".join(lines)

    return text


# =========================================================
# /TODAY
# =========================================================

async def today_command(
    update,
    context,
):
    if update.effective_chat.type not in (
        ChatType.GROUP,
        ChatType.SUPERGROUP,
    ):
        await update.effective_message.reply_text(
            "ဒီ Command ကို Group ထဲမှာ အသုံးပြုပါ။"
        )
        return

    text = await show_day(
        update,
        "today",
    )

    await update.effective_message.reply_text(
        text,
        parse_mode="HTML",
    )


# =========================================================
# /TOMORROW
# =========================================================

async def tomorrow_command(
    update,
    context,
):
    if update.effective_chat.type not in (
        ChatType.GROUP,
        ChatType.SUPERGROUP,
    ):
        await update.effective_message.reply_text(
            "ဒီ Command ကို Group ထဲမှာ အသုံးပြုပါ။"
        )
        return

    text = await show_day(
        update,
        "tomorrow",
    )

    await update.effective_message.reply_text(
        text,
        parse_mode="HTML",
    )


# =========================================================
# /SCHEDULE
# =========================================================

async def schedule_command(
    update,
    context,
):
    if not await is_admin(update):
        await update.effective_message.reply_text(
            "⛔ <b>Admin Only</b>",
            parse_mode="HTML",
        )
        return

    if not context.args:
        await update.effective_message.reply_text(
            """
╔══ 🗓 <b>ADD SCHEDULE</b> ══╗

Format:

<code>/schedule Today 1:30-3:00 day</code>

or

<code>/schedule Tomorrow 8:00-12:00 night</code>
""",
            parse_mode="HTML",
        )
        return

    text = " ".join(context.args)

    await add_schedule_from_text(
        update,
        context,
        text,
    )


# =========================================================
# /MYSCHEDULE
# =========================================================

async def my_schedule_command(
    update,
    context,
):
    user = update.effective_user
    chat = update.effective_chat

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            date,
            start_time,
            end_time,
            shift
        FROM schedules
        WHERE chat_id = ?
        AND user_id = ?
        AND date >= ?
        ORDER BY date, start_time
    """, (
        chat.id,
        user.id,
        datetime.now(TZ).date().isoformat(),
    ))

    rows = cur.fetchall()
    conn.close()

    if not rows:
        await update.effective_message.reply_text(
            "📭 Your schedule is empty."
        )
        return

    lines = [
        "╔══ 📋 <b>MY SCHEDULE</b> ══╗",
        "",
    ]

    for row in rows:
        lines.append(
            f"📅 <b>{row[0]}</b>\n"
            f"🕐 <b>{row[1]} – {row[2]}</b>\n"
            f"🌙 {row[3].upper()}\n"
            f"━━━━━━━━━━━━━━━━"
        )

    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
    )


# =========================================================
# /ADMINS
# =========================================================

async def admins_command(
    update,
    context,
):
    chat = update.effective_chat

    if chat.type not in (
        ChatType.GROUP,
        ChatType.SUPERGROUP,
    ):
        return

    try:
        admins = await context.bot.get_chat_administrators(
            chat.id
        )

        lines = [
            "╔══ 👑 <b>GROUP ADMINS</b> ══╗",
            "",
        ]

        for member in admins:

            user = member.user

            name = (
                user.full_name
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )

            mention = (
                f'<a href="tg://user?id={user.id}">'
                f'{name}</a>'
            )

            if member.status == "creator":
                role = "👑 Owner"
            else:
                role = "🛡 Admin"

            lines.append(
                f"{role} — {mention}"
            )

        await update.effective_message.reply_text(
            "\n".join(lines),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.error(
            "Admins error: %s",
            e,
        )


# =========================================================
# /REPORT
# =========================================================

async def report_command(
    update,
    context,
):
    chat = update.effective_chat

    if not await is_admin(update):
        await update.effective_message.reply_text(
            "⛔ <b>Admin Only</b>",
            parse_mode="HTML",
        )
        return

    conn = db()
    cur = conn.cursor()

    today = datetime.now(TZ).date().isoformat()

    cur.execute("""
        SELECT COUNT(*)
        FROM schedules
        WHERE chat_id = ?
        AND date = ?
    """, (
        chat.id,
        today,
    ))

    schedule_count = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM schedules
        WHERE chat_id = ?
        AND date >= ?
    """, (
        chat.id,
        today,
    ))

    upcoming = cur.fetchone()[0]

    conn.close()

    await update.effective_message.reply_text(
        f"""
╔══ 📊 <b>DAILY REPORT</b> ══╗

📅 <b>{today}</b>

🗓 Today's Schedules:
<b>{schedule_count}</b>

📋 Upcoming Schedules:
<b>{upcoming}</b>

━━━━━━━━━━━━━━━━━━━━
""",
        parse_mode="HTML",
    )


# =========================================================
# /CANCELSCHEDULE
# =========================================================

async def cancel_schedule_command(
    update,
    context,
):
    user = update.effective_user
    chat = update.effective_chat

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            date,
            start_time,
            end_time,
            shift
        FROM schedules
        WHERE chat_id = ?
        AND user_id = ?
        AND date >= ?
        ORDER BY date, start_time
    """, (
        chat.id,
        user.id,
        datetime.now(TZ).date().isoformat(),
    ))

    rows = cur.fetchall()

    if not rows:
        conn.close()

        await update.effective_message.reply_text(
            "📭 No schedules to cancel."
        )
        return

    buttons = []

    for row in rows:
        buttons.append([
            InlineKeyboardButton(
                f"❌ {row[1]} | {row[2]}-{row[3]} {row[4]}",
                callback_data=f"cancel:{row[0]}",
            )
        ])

    conn.close()

    await update.effective_message.reply_text(
        "🗑 <b>Select schedule to cancel</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# =========================================================
# /ADDADMIN
# =========================================================

async def add_admin_command(
    update,
    context,
):
    if update.effective_user.id != OWNER_ID:
        await update.effective_message.reply_text(
            "⛔ Owner Only"
        )
        return

    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /addadmin USER_ID"
        )
        return

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text(
            "❌ Invalid User ID"
        )
        return

    chat = update.effective_chat

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        INSERT OR IGNORE INTO group_admins
        (chat_id, user_id, added_at)
        VALUES (?, ?, ?)
    """, (
        chat.id,
        user_id,
        datetime.now(TZ).isoformat(),
    ))

    conn.commit()
    conn.close()

    await update.effective_message.reply_text(
        f"✅ Added admin ID: <code>{user_id}</code>",
        parse_mode="HTML",
    )


# =========================================================
# /REMOVEADMIN
# =========================================================

async def remove_admin_command(
    update,
    context,
):
    if update.effective_user.id != OWNER_ID:
        await update.effective_message.reply_text(
            "⛔ Owner Only"
        )
        return

    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /removeadmin USER_ID"
        )
        return

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text(
            "❌ Invalid User ID"
        )
        return

    chat = update.effective_chat

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM group_admins
        WHERE chat_id = ?
        AND user_id = ?
    """, (
        chat.id,
        user_id,
    ))

    conn.commit()
    conn.close()

    await update.effective_message.reply_text(
        f"✅ Removed admin ID: <code>{user_id}</code>",
        parse_mode="HTML",
    )


# =========================================================
# /GROUPID
# =========================================================

async def group_id_command(
    update,
    context,
):
    await update.effective_message.reply_text(
        f"🆔 Group ID:\n<code>{update.effective_chat.id}</code>",
        parse_mode="HTML",
    )


# =========================================================
# /CHECKADMIN
# =========================================================

async def check_admin_command(
    update,
    context,
):
    if await is_admin(update):
        await update.effective_message.reply_text(
            "✅ မင်းက ဒီ Group ရဲ့ Admin ဖြစ်ပါတယ်။"
        )
    else:
        await update.effective_message.reply_text(
            "❌ မင်းက ဒီ Group ရဲ့ Admin မဟုတ်ပါဘူး။"
        )


# =========================================================
# /BROADCAST
# =========================================================

async def broadcast_command(
    update,
    context,
):
    if update.effective_user.id != OWNER_ID:
        await update.effective_message.reply_text(
            "⛔ Owner Only"
        )
        return

    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /broadcast MESSAGE"
        )
        return

    message = " ".join(context.args)

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT user_id
        FROM users
    """)

    users = cur.fetchall()

    cur.execute("""
        SELECT chat_id
        FROM groups
    """)

    groups = cur.fetchall()

    conn.close()

    sent = 0
    failed = 0

    targets = set()

    for row in users:
        targets.add(row[0])

    for row in groups:
        targets.add(row[0])

    for target in targets:

        try:
            await context.bot.send_message(
                chat_id=target,
                text=message,
            )

            sent += 1

        except Exception:
            failed += 1

    await update.effective_message.reply_text(
        f"""
📢 <b>BROADCAST COMPLETE</b>

✅ Sent: <b>{sent}</b>
❌ Failed: <b>{failed}</b>
""",
        parse_mode="HTML",
    )


# =========================================================
# BUTTON HANDLER
# =========================================================

async def button_handler(
    update,
    context,
):
    query = update.callback_query

    await query.answer()

    data = query.data

    if data == "today":

        text = await show_day(
            query,
            "today",
        )

        await query.message.reply_text(
            text,
            parse_mode="HTML",
        )

    elif data == "tomorrow":

        text = await show_day(
            query,
            "tomorrow",
        )

        await query.message.reply_text(
            text,
            parse_mode="HTML",
        )

    elif data == "add_schedule":

        await query.message.reply_text(
            """
╔══ 🗓 <b>ADD SCHEDULE</b> ══╗

Group ထဲမှာ ဒီလိုရိုက်ပါ -

<code>Today 1:30-3:00 day</code>

သို့မဟုတ်

<code>Tomorrow 8:00-12:00 night</code>

━━━━━━━━━━━━━━━━━━━━

⏰ Schedule မစခင်
<b>15 minutes</b> ကြို Reminder ပေးပါမယ်။
""",
            parse_mode="HTML",
        )

    elif data == "myschedule":

        fake_update = update

        await my_schedule_command(
            fake_update,
            context,
        )

    elif data == "admins":

        await admins_command(
            update,
            context,
        )

    elif data == "report":

        await report_command(
            update,
            context,
        )

    elif data == "help":

        await query.message.reply_text(
            """
╔══ ℹ️ <b>HELP</b> ══╗

🗓 Add Schedule:
<code>Today 1:30-3:00 day</code>

🌙 Tomorrow:
<code>Tomorrow 8:00-12:00 night</code>

📋 /myschedule
👑 /admins
📊 /report
❌ /cancelschedule
🆔 /groupid
🛡 /checkadmin
""",
            parse_mode="HTML",
        )

    elif data.startswith("cancel:"):

        schedule_id = int(
            data.split(":")[1]
        )

        conn = db()
        cur = conn.cursor()

        cur.execute("""
            DELETE FROM schedules
            WHERE id = ?
        """, (schedule_id,))

        conn.commit()
        conn.close()

        await query.message.reply_text(
            "✅ Schedule cancelled."
        )


# =========================================================
# MEMBER TRACKING
# =========================================================

async def member_update_handler(
    update,
    context,
):
    chat_member = update.chat_member

    if not chat_member:
        return

    chat = chat_member.chat

    save_group(chat)

    user = chat_member.new_chat_member.user

    save_user(user)


# =========================================================
# CLEANUP
# =========================================================

async def cleanup_job(
    context,
):
    today = datetime.now(TZ).date()

    old_date = (
        today - timedelta(days=30)
    ).isoformat()

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM schedules
        WHERE date < ?
    """, (old_date,))

    conn.commit()
    conn.close()


# =========================================================
# POST INIT
# =========================================================

async def post_init(
    application,
):
    global current_application

    current_application = application

    await restore_jobs(
        application
    )

    application.job_queue.run_repeating(
        cleanup_job,
        interval=86400,
        first=60,
        name="cleanup",
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing."
        )

    init_db()

    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # -------------------------------
    # COMMANDS
    # -------------------------------

    application.add_handler(
        CommandHandler(
            "start",
            start_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "today",
            today_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "tomorrow",
            tomorrow_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "schedule",
            schedule_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "myschedule",
            my_schedule_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "admins",
            admins_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "report",
            report_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "cancelschedule",
            cancel_schedule_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "addadmin",
            add_admin_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "removeadmin",
            remove_admin_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "groupid",
            group_id_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "checkadmin",
            check_admin_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "broadcast",
            broadcast_command,
        )
    )

    # -------------------------------
    # BUTTONS
    # -------------------------------

    application.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    # -------------------------------
    # RAW SCHEDULE TEXT
    # -------------------------------

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            schedule_text_handler,
        )
    )

    # -------------------------------
    # MEMBER TRACKING
    # -------------------------------

    application.add_handler(
        ChatMemberHandler(
            member_update_handler,
            ChatMemberHandler.CHAT_MEMBER,
        )
    )

    print(
        "🤖 Group Admin Management Bot is running..."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
