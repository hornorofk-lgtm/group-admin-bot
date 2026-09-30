import os
import re
import sqlite3
import logging
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
)

# =========================================================
# CONFIG
# =========================================================

TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "bot.db")

TZ = ZoneInfo("Asia/Yangon")
REMINDER_MINUTES = 15

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# DATABASE
# =========================================================

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
            first_seen TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            chat_id INTEGER PRIMARY KEY,
            title TEXT,
            chat_type TEXT,
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

    cur.execute("""
        CREATE TABLE IF NOT EXISTS schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            user_id INTEGER,
            start_at TEXT,
            end_at TEXT,
            shift TEXT,
            note TEXT,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_id INTEGER,
            user_id INTEGER,
            status TEXT,
            checked_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS member_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            user_id INTEGER,
            event TEXT,
            event_at TEXT
        )
    """)

    conn.commit()
    conn.close()


def now():
    return datetime.now(TZ)


def to_iso(dt):
    return dt.astimezone(TZ).isoformat()


# =========================================================
# SAVE USER / GROUP
# =========================================================

def save_user(user):
    if not user:
        return

    conn = get_db()

    conn.execute("""
        INSERT INTO users
        (user_id, username, full_name, first_seen)
        VALUES (?, ?, ?, ?)

        ON CONFLICT(user_id)
        DO UPDATE SET
            username = excluded.username,
            full_name = excluded.full_name
    """, (
        user.id,
        user.username or "",
        user.full_name or "",
        to_iso(now()),
    ))

    conn.commit()
    conn.close()


def save_group(chat):
    if not chat:
        return

    conn = get_db()

    conn.execute("""
        INSERT INTO groups
        (chat_id, title, chat_type, created_at)
        VALUES (?, ?, ?, ?)

        ON CONFLICT(chat_id)
        DO UPDATE SET
            title = excluded.title,
            chat_type = excluded.chat_type
    """, (
        chat.id,
        chat.title or "",
        chat.type,
        to_iso(now()),
    ))

    conn.commit()
    conn.close()


# =========================================================
# PREMIUM MENU
# =========================================================

async def get_menu():
    bot = await current_application.bot.get_me()
    username = bot.username

    group_url = f"https://t.me/{username}?startgroup=true"
    channel_url = f"https://t.me/{username}?startchannel=true"

    keyboard = [
        [
            InlineKeyboardButton(
                "✦ TODAY",
                callback_data="today"
            ),
            InlineKeyboardButton(
                "✦ TOMORROW",
                callback_data="tomorrow"
            ),
        ],
        [
            InlineKeyboardButton(
                "＋ ADD SCHEDULE",
                callback_data="add_schedule"
            ),
            InlineKeyboardButton(
                "◈ MY SCHEDULE",
                callback_data="my_schedule"
            ),
        ],
        [
            InlineKeyboardButton(
                "♛ ADMINS",
                callback_data="admins"
            ),
            InlineKeyboardButton(
                "▣ REPORTS",
                callback_data="report"
            ),
        ],
        [
            InlineKeyboardButton(
                "＋ ADD GROUP",
                url=group_url
            ),
            InlineKeyboardButton(
                "＋ ADD CHANNEL",
                url=channel_url
            ),
        ],
        [
            InlineKeyboardButton(
                "❔ HELP",
                callback_data="help"
            ),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================================================
# START MESSAGE
# =========================================================

def welcome_text(chat_title):
    return (
        "╔════════════════════════════╗\n"
        "        ✦ GROUP ADMIN HUB ✦\n"
        "╚════════════════════════════╝\n\n"

        f"▸ <b>{chat_title}</b>\n\n"

        "<blockquote>"
        "Premium Admin Management System\n\n"
        "• Admin Schedule\n"
        "• 15-Minute Reminder\n"
        "• Start / End Notification\n"
        "• Admin List\n"
        "• Daily Reports\n"
        "• Member Tracking\n"
        "• Multi-Group Support"
        "</blockquote>\n\n"

        "Choose an option below."
    )


# =========================================================
# ADMIN CHECK
# =========================================================

async def is_admin(update, user_id=None):

    chat = update.effective_chat

    if not chat:
        return False

    if user_id is None:
        user_id = update.effective_user.id

    try:
        member = await chat.get_member(user_id)

        return member.status in (
            "administrator",
            "creator",
            "owner",
        )

    except Exception:
        return False


async def require_admin(update):

    if await is_admin(update):
        return True

    if update.effective_message:
        await update.effective_message.reply_text(
            "⛔ <b>Admin Only</b>\n\n"
            "ဒီလုပ်ဆောင်ချက်ကို Group Admin တွေပဲ အသုံးပြုနိုင်ပါတယ်။",
            parse_mode="HTML",
        )

    return False


# =========================================================
# TIME PARSER
# =========================================================

def convert_time(hour, minute, shift):

    hour = int(hour)
    minute = int(minute)

    if hour > 23 or minute > 59:
        raise ValueError("Invalid time.")

    shift = shift.lower()

    # DAY
    if shift == "day":

        # 1:00 -> 13:00
        if hour < 8:
            hour += 12

    # NIGHT
    elif shift == "night":

        # 8:00 -> 20:00
        if 1 <= hour <= 11:
            hour += 12

        # 12:00 -> 00:00
        elif hour == 12:
            hour = 0

    return time(hour, minute)


def parse_schedule(text):

    pattern = re.compile(
        r"^(today|tomorrow)\s+"
        r"(\d{1,2}):(\d{2})"
        r"\s*-\s*"
        r"(\d{1,2})(?::(\d{2}))?"
        r"\s+"
        r"(day|night)"
        r"(?:\s*\|\s*(.*))?$",
        re.IGNORECASE,
    )

    match = pattern.match(text.strip())

    if not match:
        raise ValueError(
            "Format မှားနေပါတယ်။\n\n"
            "ဥပမာ:\n"
            "Today 12:15-1:00 day\n"
            "Tomorrow 8:00-12:00 night\n\n"
            "Note ပါချင်ရင်:\n"
            "Today 12:15-1:00 day | meeting"
        )

    day_word = match.group(1).lower()

    start_hour = match.group(2)
    start_minute = match.group(3)

    end_hour = match.group(4)
    end_minute = match.group(5) or "00"

    shift = match.group(6).lower()
    note = match.group(7) or ""

    start_time = convert_time(
        start_hour,
        start_minute,
        shift,
    )

    end_time = convert_time(
        end_hour,
        end_minute,
        shift,
    )

    base_date = now().date()

    if day_word == "tomorrow":
        base_date += timedelta(days=1)

    start_dt = datetime.combine(
        base_date,
        start_time,
        tzinfo=TZ,
    )

    end_dt = datetime.combine(
        base_date,
        end_time,
        tzinfo=TZ,
    )

    if end_dt <= start_dt:
        end_dt += timedelta(days=1)

    return (
        start_dt,
        end_dt,
        shift,
        note.strip(),
    )


# =========================================================
# SCHEDULE JOBS
# =========================================================

def job_name(prefix, schedule_id):
    return f"{prefix}_{schedule_id}"


def remove_schedule_jobs(app, schedule_id):

    for prefix in (
        "reminder",
        "start",
        "end",
    ):

        jobs = app.job_queue.get_jobs_by_name(
            job_name(prefix, schedule_id)
        )

        for job in jobs:
            job.schedule_removal()


def create_schedule_jobs(app, row):

    start_dt = datetime.fromisoformat(
        row["start_at"]
    ).astimezone(TZ)

    end_dt = datetime.fromisoformat(
        row["end_at"]
    ).astimezone(TZ)

    current = now()

    reminder_time = (
        start_dt -
        timedelta(minutes=REMINDER_MINUTES)
    )

    if reminder_time > current:

        app.job_queue.run_once(
            reminder_job,
            when=reminder_time,
            data={
                "schedule_id": row["id"]
            },
            name=job_name(
                "reminder",
                row["id"]
            ),
        )

    if start_dt > current:

        app.job_queue.run_once(
            start_job,
            when=start_dt,
            data={
                "schedule_id": row["id"]
            },
            name=job_name(
                "start",
                row["id"]
            ),
        )

    if end_dt > current:

        app.job_queue.run_once(
            end_job,
            when=end_dt,
            data={
                "schedule_id": row["id"]
            },
            name=job_name(
                "end",
                row["id"]
            ),
        )


# =========================================================
# NOTIFICATIONS
# =========================================================

async def send_notification(
    context,
    schedule_id,
    notification_type,
):

    conn = get_db()

    row = conn.execute("""
        SELECT *
        FROM schedules
        WHERE id = ?
    """, (schedule_id,)).fetchone()

    if not row:
        conn.close()
        return

    user = conn.execute("""
        SELECT *
        FROM users
        WHERE user_id = ?
    """, (row["user_id"],)).fetchone()

    conn.close()

    name = (
        user["full_name"]
        if user
        else str(row["user_id"])
    )

    start_dt = datetime.fromisoformat(
        row["start_at"]
    ).astimezone(TZ)

    end_dt = datetime.fromisoformat(
        row["end_at"]
    ).astimezone(TZ)

    time_text = (
        f"{start_dt.strftime('%I:%M %p')}"
        f" – "
        f"{end_dt.strftime('%I:%M %p')}"
    )

    if notification_type == "reminder":

        title = "⏰ SHIFT REMINDER"

        message = (
            f"Admin <b>{name}</b>\n\n"
            f"Your shift starts in "
            f"<b>{REMINDER_MINUTES} minutes</b>."
        )

    elif notification_type == "start":

        title = "🔔 SHIFT STARTED"

        message = (
            f"Admin <b>{name}</b>\n\n"
            "Your scheduled shift has started."
        )

    else:

        title = "🏁 SHIFT ENDED"

        message = (
            f"Admin <b>{name}</b>\n\n"
            "Your scheduled shift has ended."
        )

    text = (
        f"╔══ {title} ══╗\n\n"
        f"{message}\n\n"
        f"🕐 {time_text}\n"
        f"🌙 {row['shift'].upper()}"
    )

    if row["note"]:
        text += (
            f"\n📝 {row['note']}"
        )

    try:

        await context.bot.send_message(
            chat_id=row["chat_id"],
            text=text,
            parse_mode="HTML",
        )

    except Exception as e:

        logger.warning(
            "Notification failed: %s",
            e
        )

    if notification_type == "start":

        conn = get_db()

        conn.execute("""
            INSERT INTO attendance
            (schedule_id, user_id, status, checked_at)
            VALUES (?, ?, ?, ?)
        """, (
            schedule_id,
            row["user_id"],
            "started",
            to_iso(now()),
        ))

        conn.commit()
        conn.close()


async def reminder_job(context):
    await send_notification(
        context,
        context.job.data["schedule_id"],
        "reminder",
    )


async def start_job(context):
    await send_notification(
        context,
        context.job.data["schedule_id"],
        "start",
    )


async def end_job(context):
    await send_notification(
        context,
        context.job.data["schedule_id"],
        "end",
    )


# =========================================================
# RESTORE JOBS AFTER RESTART
# =========================================================

async def restore_jobs(app):

    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM schedules
        WHERE end_at > ?
        ORDER BY start_at
    """, (
        to_iso(now()),
    )).fetchall()

    conn.close()

    for row in rows:
        create_schedule_jobs(
            app,
            row,
        )

    logger.info(
        "Restored %s schedule(s).",
        len(rows),
    )


# =========================================================
# START
# =========================================================

async def start_command(
    update,
    context,
):

    save_user(
        update.effective_user
    )

    chat = update.effective_chat

    if chat.type in (
        ChatType.GROUP,
        ChatType.SUPERGROUP,
        ChatType.CHANNEL,
    ):
        save_group(chat)

    menu = await get_menu()

    await update.message.reply_text(
        welcome_text(
            chat.title
            or "Group Admin Hub"
        ),
        parse_mode="HTML",
        reply_markup=menu,
    )


# =========================================================
# TODAY
# =========================================================

async def show_day(
    update,
    day,
):

    chat_id = update.effective_chat.id

    start = datetime.combine(
        day,
        time.min,
        tzinfo=TZ,
    )

    end = start + timedelta(days=1)

    conn = get_db()

    rows = conn.execute("""
        SELECT
            schedules.*,
            users.username,
            users.full_name

        FROM schedules

        LEFT JOIN users
        ON users.user_id = schedules.user_id

        WHERE schedules.chat_id = ?
        AND schedules.start_at >= ?
        AND schedules.start_at < ?

        ORDER BY schedules.start_at
    """, (
        chat_id,
        to_iso(start),
        to_iso(end),
    )).fetchall()

    conn.close()

    title = (
        "TODAY"
        if day == now().date()
        else "TOMORROW"
    )

    if not rows:

        text = (
            f"╔══ ✦ {title} SCHEDULE ✦ ══╗\n\n"
            "No schedule found.\n\n"
            "＋ ADD SCHEDULE ကိုနှိပ်ပြီး "
            "Schedule ထည့်နိုင်ပါတယ်။"
        )

    else:

        lines = [
            f"╔══ ✦ {title} SCHEDULE ✦ ══╗",
            "",
        ]

        for index, row in enumerate(
            rows,
            1,
        ):

            start_dt = datetime.fromisoformat(
                row["start_at"]
            ).astimezone(TZ)

            end_dt = datetime.fromisoformat(
                row["end_at"]
            ).astimezone(TZ)

            username = (
                f"@{row['username']}"
                if row["username"]
                else row["full_name"]
            )

            lines.append(
                f"<b>{index}. "
                f"{start_dt.strftime('%I:%M %p')} – "
                f"{end_dt.strftime('%I:%M %p')}</b>"
            )

            lines.append(
                f"👤 {username}"
            )

            lines.append(
                f"🌙 {row['shift'].upper()}"
            )

            if row["note"]:
                lines.append(
                    f"📝 {row['note']}"
                )

            lines.append("")

        text = "\n".join(lines)

    menu = await get_menu()

    await update.effective_message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=menu,
    )


async def today_command(
    update,
    context,
):

    if update.effective_chat.type in (
        ChatType.GROUP,
        ChatType.SUPERGROUP,
    ):
        save_group(
            update.effective_chat
        )

    await show_day(
        update,
        now().date(),
    )


async def tomorrow_command(
    update,
    context,
):

    if update.effective_chat.type in (
        ChatType.GROUP,
        ChatType.SUPERGROUP,
    ):
        save_group(
            update.effective_chat
        )

    await show_day(
        update,
        now().date()
        + timedelta(days=1),
    )


# =========================================================
# ADD SCHEDULE
# =========================================================

async def schedule_command(
    update,
    context,
):

    if not await require_admin(update):
        return

    if not context.args:

        await update.message.reply_text(
            "＋ <b>ADD SCHEDULE</b>\n\n"
            "အသုံးပြုပုံ:\n\n"
            "<code>Today 12:15-1:00 day</code>\n"
            "<code>Tomorrow 8:00-12:00 night</code>\n\n"
            "Note ပါချင်ရင်:\n"
            "<code>Today 12:15-1:00 day | meeting</code>",
            parse_mode="HTML",
        )

        return

    schedule_text = " ".join(
        context.args
    )

    try:

        start_dt, end_dt, shift, note = (
            parse_schedule(schedule_text)
        )

    except ValueError as e:

        await update.message.reply_text(
            f"❌ {e}"
        )

        return

    save_user(
        update.effective_user
    )

    save_group(
        update.effective_chat
    )

    conn = get_db()

    cursor = conn.execute("""
        INSERT INTO schedules
        (
            chat_id,
            user_id,
            start_at,
            end_at,
            shift,
            note,
            created_at
        )

        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        update.effective_chat.id,
        update.effective_user.id,
        to_iso(start_dt),
        to_iso(end_dt),
        shift,
        note,
        to_iso(now()),
    ))

    schedule_id = cursor.lastrowid

    conn.commit()

    row = conn.execute("""
        SELECT *
        FROM schedules
        WHERE id = ?
    """, (
        schedule_id,
    )).fetchone()

    conn.close()

    create_schedule_jobs(
        context.application,
        row,
    )

    text = (
        "╔══ ✦ SCHEDULE ADDED ✦ ══╗\n\n"
        f"🕐 <b>{start_dt.strftime('%I:%M %p')} "
        f"– {end_dt.strftime('%I:%M %p')}</b>\n"
        f"🌙 {shift.upper()}\n"
        f"👤 {update.effective_user.full_name}\n"
    )

    if note:
        text += (
            f"📝 {note}\n"
        )

    text += (
        "\n⏰ Reminder: "
        f"{REMINDER_MINUTES} minutes before"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
    )


# =========================================================
# MY SCHEDULE
# =========================================================

async def my_schedule_command(
    update,
    context,
):

    save_user(
        update.effective_user
    )

    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM schedules

        WHERE chat_id = ?
        AND user_id = ?
        AND end_at > ?

        ORDER BY start_at
    """, (
        update.effective_chat.id,
        update.effective_user.id,
        to_iso(now()),
    )).fetchall()

    conn.close()

    if not rows:

        await update.effective_message.reply_text(
            "◈ <b>MY SCHEDULE</b>\n\n"
            "No upcoming schedule.",
            parse_mode="HTML",
        )

        return

    lines = [
        "╔══ ✦ MY SCHEDULE ✦ ══╗",
        "",
    ]

    for index, row in enumerate(
        rows,
        1,
    ):

        start_dt = datetime.fromisoformat(
            row["start_at"]
        ).astimezone(TZ)

        end_dt = datetime.fromisoformat(
            row["end_at"]
        ).astimezone(TZ)

        lines.append(
            f"<b>{index}. "
            f"{start_dt.strftime('%I:%M %p')} – "
            f"{end_dt.strftime('%I:%M %p')}</b>"
        )

        lines.append(
            f"🌙 {row['shift'].upper()}"
        )

        if row["note"]:
            lines.append(
                f"📝 {row['note']}"
            )

        lines.append("")

    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
    )


# =========================================================
# ADMINS
# =========================================================

async def admins_command(
    update,
    context,
):

    if update.effective_chat.type not in (
        ChatType.GROUP,
        ChatType.SUPERGROUP,
    ):

        await update.message.reply_text(
            "ဒီ command ကို Group ထဲမှာ သုံးပါ။"
        )

        return

    admins = await update.effective_chat.get_administrators()

    lines = [
        "╔══ ✦ GROUP ADMINS ✦ ══╗",
        "",
    ]

    for index, admin in enumerate(
        admins,
        1,
    ):

        user = admin.user

        name = (
            f"@{user.username}"
            if user.username
            else user.full_name
        )

        role = (
            "OWNER"
            if admin.status == "creator"
            else "ADMIN"
        )

        lines.append(
            f"{index}. {name} — <b>{role}</b>"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
    )


# =========================================================
# REPORT
# =========================================================

async def report_command(
    update,
    context,
):

    if not await require_admin(update):
        return

    chat_id = update.effective_chat.id

    today = now().date()

    start = datetime.combine(
        today,
        time.min,
        tzinfo=TZ,
    )

    end = start + timedelta(days=1)

    conn = get_db()

    schedule_count = conn.execute("""
        SELECT COUNT(*) AS count
        FROM schedules
        WHERE chat_id = ?
        AND start_at >= ?
        AND start_at < ?
    """, (
        chat_id,
        to_iso(start),
        to_iso(end),
    )).fetchone()["count"]

    joins = conn.execute("""
        SELECT COUNT(*) AS count
        FROM member_events
        WHERE chat_id = ?
        AND event = 'join'
        AND event_at >= ?
    """, (
        chat_id,
        to_iso(start),
    )).fetchone()["count"]

    leaves = conn.execute("""
        SELECT COUNT(*) AS count
        FROM member_events
        WHERE chat_id = ?
        AND event = 'leave'
        AND event_at >= ?
    """, (
        chat_id,
        to_iso(start),
    )).fetchone()["count"]

    conn.close()

    await update.message.reply_text(
        "╔══ ✦ DAILY REPORT ✦ ══╗\n\n"
        f"📅 {today.strftime('%d %B %Y')}\n\n"
        f"📅 Schedules: <b>{schedule_count}</b>\n"
        f"🟢 Joined: <b>{joins}</b>\n"
        f"🔴 Left: <b>{leaves}</b>",
        parse_mode="HTML",
    )


# =========================================================
# CANCEL
# =========================================================

async def cancel_schedule_command(
    update,
    context,
):

    if not await require_admin(update):
        return

    if not context.args:

        await update.message.reply_text(
            "အသုံးပြုပုံ:\n"
            "<code>/cancelschedule 123</code>",
            parse_mode="HTML",
        )

        return

    try:
        schedule_id = int(
            context.args[0]
        )
    except ValueError:

        await update.message.reply_text(
            "❌ Schedule ID မှားနေပါတယ်။"
        )

        return

    conn = get_db()

    row = conn.execute("""
        SELECT *
        FROM schedules
        WHERE id = ?
        AND chat_id = ?
    """, (
        schedule_id,
        update.effective_chat.id,
    )).fetchone()

    if not row:

        conn.close()

        await update.message.reply_text(
            "❌ Schedule မတွေ့ပါ။"
        )

        return

    conn.execute("""
        DELETE FROM schedules
        WHERE id = ?
    """, (
        schedule_id,
    ))

    conn.commit()
    conn.close()

    remove_schedule_jobs(
        context.application,
        schedule_id,
    )

    await update.message.reply_text(
        f"✅ Schedule #{schedule_id} ဖျက်ပြီးပါပြီ။"
    )


# =========================================================
# ADD / REMOVE ADMIN
# =========================================================

async def add_admin_command(
    update,
    context,
):

    if not await require_admin(update):
        return

    if not context.args:

        await update.message.reply_text(
            "အသုံးပြုပုံ:\n"
            "<code>/addadmin USER_ID</code>",
            parse_mode="HTML",
        )

        return

    try:
        user_id = int(
            context.args[0]
        )
    except ValueError:

        await update.message.reply_text(
            "❌ User ID မှားနေပါတယ်။"
        )

        return

    conn = get_db()

    conn.execute("""
        INSERT OR IGNORE INTO group_admins
        (chat_id, user_id, added_at)
        VALUES (?, ?, ?)
    """, (
        update.effective_chat.id,
        user_id,
        to_iso(now()),
    ))

    conn.commit()
    conn.close()

    await update.message.reply_text(
        "✅ Bot admin list ထဲ ထည့်ပြီးပါပြီ။\n\n"
        "မှတ်ချက် — ဒီ command က Telegram ရဲ့ "
        "Admin permission ကို မပြောင်းပေးပါ။"
    )


async def remove_admin_command(
    update,
    context,
):

    if not await require_admin(update):
        return

    if not context.args:

        await update.message.reply_text(
            "အသုံးပြုပုံ:\n"
            "<code>/removeadmin USER_ID</code>",
            parse_mode="HTML",
        )

        return

    try:
        user_id = int(
            context.args[0]
        )
    except ValueError:

        await update.message.reply_text(
            "❌ User ID မှားနေပါတယ်။"
        )

        return

    conn = get_db()

    conn.execute("""
        DELETE FROM group_admins
        WHERE chat_id = ?
        AND user_id = ?
    """, (
        update.effective_chat.id,
        user_id,
    ))

    conn.commit()
    conn.close()

    await update.message.reply_text(
        "✅ Bot admin list ကနေ ဖယ်ရှားပြီးပါပြီ။"
    )


# =========================================================
# GROUP ID
# =========================================================

async def groupid_command(
    update,
    context,
):

    save_group(
        update.effective_chat
    )

    await update.message.reply_text(
        "🆔 <b>Group ID</b>\n\n"
        f"<code>{update.effective_chat.id}</code>",
        parse_mode="HTML",
    )


# =========================================================
# CHECK ADMIN
# =========================================================

async def checkadmin_command(
    update,
    context,
):

    if await is_admin(update):

        await update.message.reply_text(
            "✅ <b>မင်းက ဒီ Group ရဲ့ Admin ဖြစ်ပါတယ်။</b>",
            parse_mode="HTML",
        )

    else:

        await update.message.reply_text(
            "❌ မင်းက ဒီ Group ရဲ့ Admin မဟုတ်ပါဘူး။",
            parse_mode="HTML",
        )


# =========================================================
# BROADCAST
# =========================================================

async def broadcast_command(
    update,
    context,
):

    if update.effective_user.id != OWNER_ID:

        await update.message.reply_text(
            "⛔ Owner only."
        )

        return

    if not update.message.reply_to_message:

        await update.message.reply_text(
            "Broadcast လုပ်မယ့် message ကို "
            "Reply လုပ်ပြီး /broadcast ပို့ပါ။"
        )

        return

    conn = get_db()

    users = conn.execute("""
        SELECT user_id
        FROM users
    """).fetchall()

    groups = conn.execute("""
        SELECT chat_id
        FROM groups
    """).fetchall()

    conn.close()

    targets = set()

    for row in users:
        targets.add(
            row["user_id"]
        )

    for row in groups:
        targets.add(
            row["chat_id"]
        )

    sent = 0
    failed = 0

    for chat_id in targets:

        try:

            await update.message.reply_to_message.copy(
                chat_id=chat_id
            )

            sent += 1

        except Exception:

            failed += 1

    await update.message.reply_text(
        "╔══ ✦ BROADCAST ✦ ══╗\n\n"
        f"✅ Sent: <b>{sent}</b>\n"
        f"❌ Failed: <b>{failed}</b>",
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

        await show_day(
            query,
            now().date(),
        )

        return

    if data == "tomorrow":

        await show_day(
            query,
            now().date()
            + timedelta(days=1),
        )

        return

    if data == "add_schedule":

        await query.message.reply_text(
            "＋ <b>ADD SCHEDULE</b>\n\n"
            "ဒီလိုပို့ပါ:\n\n"
            "<code>Today 12:15-1:00 day</code>\n"
            "<code>Tomorrow 8:00-12:00 night</code>\n\n"
            "Note:\n"
            "<code>Today 12:15-1:00 day | meeting</code>",
            parse_mode="HTML",
        )

        return

    if data == "my_schedule":

        rows = []

        conn = get_db()

        rows = conn.execute("""
            SELECT *
            FROM schedules
            WHERE chat_id = ?
            AND user_id = ?
            AND end_at > ?
            ORDER BY start_at
        """, (
            query.message.chat.id,
            query.from_user.id,
            to_iso(now()),
        )).fetchall()

        conn.close()

        if not rows:

            await query.message.reply_text(
                "◈ <b>MY SCHEDULE</b>\n\n"
                "No upcoming schedule.",
                parse_mode="HTML",
            )

            return

        lines = [
            "╔══ ✦ MY SCHEDULE ✦ ══╗",
            "",
        ]

        for index, row in enumerate(
            rows,
            1,
        ):

            start_dt = datetime.fromisoformat(
                row["start_at"]
            ).astimezone(TZ)

            end_dt = datetime.fromisoformat(
                row["end_at"]
            ).astimezone(TZ)

            lines.append(
                f"<b>{index}. "
                f"{start_dt.strftime('%I:%M %p')} – "
                f"{end_dt.strftime('%I:%M %p')}</b>"
            )

            lines.append(
                f"🌙 {row['shift'].upper()}"
            )

            if row["note"]:
                lines.append(
                    f"📝 {row['note']}"
                )

            lines.append("")

        await query.message.reply_text(
            "\n".join(lines),
            parse_mode="HTML",
        )

        return

    if data == "admins":

        admins = await query.message.chat.get_administrators()

        lines = [
            "╔══ ✦ GROUP ADMINS ✦ ══╗",
            "",
        ]

        for index, admin in enumerate(
            admins,
            1,
        ):

            user = admin.user

            name = (
                f"@{user.username}"
                if user.username
                else user.full_name
            )

            role = (
                "OWNER"
                if admin.status == "creator"
                else "ADMIN"
            )

            lines.append(
                f"{index}. {name} — <b>{role}</b>"
            )

        await query.message.reply_text(
            "\n".join(lines),
            parse_mode="HTML",
        )

        return

    if data == "report":

        if not await is_admin(
            update,
            query.from_user.id,
        ):

            await query.message.reply_text(
                "⛔ Admin only."
            )

            return

        chat_id = query.message.chat.id

        today = now().date()

        start = datetime.combine(
            today,
            time.min,
            tzinfo=TZ,
        )

        end = start + timedelta(days=1)

        conn = get_db()

        schedule_count = conn.execute("""
            SELECT COUNT(*) AS count
            FROM schedules
            WHERE chat_id = ?
            AND start_at >= ?
            AND start_at < ?
        """, (
            chat_id,
            to_iso(start),
            to_iso(end),
        )).fetchone()["count"]

        joins = conn.execute("""
            SELECT COUNT(*) AS count
            FROM member_events
            WHERE chat_id = ?
            AND event = 'join'
            AND event_at >= ?
        """, (
            chat_id,
            to_iso(start),
        )).fetchone()["count"]

        leaves = conn.execute("""
            SELECT COUNT(*) AS count
            FROM member_events
            WHERE chat_id = ?
            AND event = 'leave'
            AND event_at >= ?
        """, (
            chat_id,
            to_iso(start),
        )).fetchone()["count"]

        conn.close()

        await query.message.reply_text(
            "╔══ ✦ DAILY REPORT ✦ ══╗\n\n"
            f"📅 {today.strftime('%d %B %Y')}\n\n"
            f"📅 Schedules: <b>{schedule_count}</b>\n"
            f"🟢 Joined: <b>{joins}</b>\n"
            f"🔴 Left: <b>{leaves}</b>",
            parse_mode="HTML",
        )

        return

    if data == "help":

        await query.message.reply_text(
            "╔══ ✦ HELP ✦ ══╗\n\n"

            "<b>Schedule</b>\n"
            "<code>Today 12:15-1:00 day</code>\n"
            "<code>Tomorrow 8:00-12:00 night</code>\n\n"

            "<b>Commands</b>\n"
            "/start\n"
            "/today\n"
            "/tomorrow\n"
            "/schedule\n"
            "/myschedule\n"
            "/cancelschedule ID\n"
            "/admins\n"
            "/report\n"
            "/addadmin USER_ID\n"
            "/removeadmin USER_ID\n"
            "/groupid\n"
            "/checkadmin\n"
            "/broadcast",
            parse_mode="HTML",
        )

        return


# =========================================================
# MEMBER TRACKING
# =========================================================

async def member_update(
    update,
    context,
):

    chat = update.effective_chat

    if not chat:
        return

    save_group(chat)

    event_data = update.chat_member

    if not event_data:
        return

    old_status = (
        event_data
        .old_chat_member
        .status
    )

    new_status = (
        event_data
        .new_chat_member
        .status
    )

    user = (
        event_data
        .new_chat_member
        .user
    )

    save_user(user)

    joined = {
        "member",
        "administrator",
        "creator",
    }

    left = {
        "left",
        "kicked",
    }

    event = None

    if (
        old_status not in joined
        and new_status in joined
    ):
        event = "join"

    elif (
        old_status in joined
        and new_status in left
    ):
        event = "leave"

    if event:

        conn = get_db()

        conn.execute("""
            INSERT INTO member_events
            (chat_id, user_id, event, event_at)
            VALUES (?, ?, ?, ?)
        """, (
            chat.id,
            user.id,
            event,
            to_iso(now()),
        ))

        conn.commit()
        conn.close()


# =========================================================
# CLEANUP
# =========================================================

async def cleanup_job(context):

    cutoff = now() - timedelta(
        days=30
    )

    conn = get_db()

    conn.execute("""
        DELETE FROM schedules
        WHERE end_at < ?
    """, (
        to_iso(cutoff),
    ))

    conn.execute("""
        DELETE FROM member_events
        WHERE event_at < ?
    """, (
        to_iso(cutoff),
    ))

    conn.commit()
    conn.close()


# =========================================================
# POST INIT
# =========================================================

async def post_init(app):

    init_db()

    await restore_jobs(app)

    app.job_queue.run_repeating(
        cleanup_job,
        interval=timedelta(days=1),
        first=timedelta(minutes=5),
        name="database_cleanup",
    )


# =========================================================
# GLOBAL APPLICATION
# =========================================================

current_application = None


# =========================================================
# MAIN
# =========================================================

def main():

    global current_application

    if not TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing."
        )

    if not OWNER_ID:
        raise RuntimeError(
            "OWNER_ID is missing."
        )

    init_db()

    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    current_application = application

    # Commands
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
            "cancelschedule",
            cancel_schedule_command,
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
            groupid_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "checkadmin",
            checkadmin_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "broadcast",
            broadcast_command,
        )
    )

    # Buttons
    application.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    # Member tracking
    application.add_handler(
        ChatMemberHandler(
            member_update,
            ChatMemberHandler.CHAT_MEMBER,
        )
    )

    logger.info(
        "🤖 Group Admin Management Bot is running..."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
