import os
import re
import sqlite3
import logging
from datetime import datetime, date, timedelta, time
from zoneinfo import ZoneInfo

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    ChatMemberHandler,
    filters,
)

# ============================================================
# CONFIG
# ============================================================

TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

DB_PATH = os.getenv("DB_PATH", "bot.db")

# Myanmar Time
TZ = ZoneInfo("Asia/Yangon")

REMINDER_MINUTES = 15


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# DATABASE
# ============================================================

def get_db():
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
    )
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
            note TEXT DEFAULT '',
            added_at TEXT,
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
            note TEXT DEFAULT '',
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_id INTEGER,
            admin_id INTEGER,
            chat_id INTEGER,
            status TEXT,
            checked_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS member_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            user_id INTEGER,
            username TEXT,
            full_name TEXT,
            event TEXT,
            created_at TEXT
        )
    """)

    conn.commit()
    conn.close()


# ============================================================
# HELPERS
# ============================================================

def now_local():
    return datetime.now(TZ)


def now_iso():
    return now_local().isoformat()


def today_local():
    return now_local().date()


def format_date(d):
    return d.strftime("%d %b %Y")


def format_time(t):
    return t.strftime("%I:%M %p").lstrip("0")


def user_display(user):
    if not user:
        return "Unknown"

    if user.username:
        return f"@{user.username}"

    return user.full_name or str(user.id)


def register_user(user):
    if not user:
        return

    conn = get_db()

    conn.execute("""
        INSERT OR REPLACE INTO users
        (
            user_id,
            username,
            full_name,
            started_at
        )
        VALUES (?, ?, ?, ?)
    """, (
        user.id,
        user.username or "",
        user.full_name or "",
        now_iso(),
    ))

    conn.commit()
    conn.close()


def register_group(chat_id, title):
    conn = get_db()

    conn.execute("""
        INSERT OR IGNORE INTO groups
        (
            chat_id,
            title,
            owner_id,
            created_at
        )
        VALUES (?, ?, ?, ?)
    """, (
        chat_id,
        title or "Unknown Group",
        None,
        now_iso(),
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


# ============================================================
# ADMIN CHECK
# ============================================================

async def is_group_admin(
    context,
    chat_id,
    user_id,
):
    try:
        member = await context.bot.get_chat_member(
            chat_id=chat_id,
            user_id=user_id,
        )

        return member.status in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )

    except Exception as e:
        logger.warning(
            "Admin check failed: %s",
            e,
        )
        return False


async def is_group_owner(
    context,
    chat_id,
    user_id,
):
    try:
        member = await context.bot.get_chat_member(
            chat_id=chat_id,
            user_id=user_id,
        )

        return member.status == ChatMemberStatus.OWNER

    except Exception:
        return False


async def require_group_admin(
    update,
    context,
):
    chat = update.effective_chat
    user = update.effective_user

    if chat.type not in (
        "group",
        "supergroup",
    ):
        await update.message.reply_text(
            "❌ ဒီ command ကို Group ထဲမှာ အသုံးပြုပါ။"
        )
        return False

    if not await is_group_admin(
        context,
        chat.id,
        user.id,
    ):
        await update.message.reply_text(
            "⛔ ဒီလုပ်ဆောင်ချက်ကို Group Admin တွေပဲ အသုံးပြုနိုင်ပါတယ်။"
        )
        return False

    register_group(
        chat.id,
        chat.title,
    )

    return True


# ============================================================
# PREMIUM KEYBOARD
# ============================================================

def main_keyboard():
    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "✦ TODAY",
                callback_data="today",
            ),
            InlineKeyboardButton(
                "✦ TOMORROW",
                callback_data="tomorrow",
            ),
        ],

        [
            InlineKeyboardButton(
                "＋ ADD SCHEDULE",
                callback_data="add",
            ),
            InlineKeyboardButton(
                "◈ MY SCHEDULE",
                callback_data="mine",
            ),
        ],

        [
            InlineKeyboardButton(
                "♛ ADMINS",
                callback_data="admins",
            ),
            InlineKeyboardButton(
                "▣ REPORTS",
                callback_data="reports",
            ),
        ],

        [
            InlineKeyboardButton(
                "⚙ SETTINGS",
                callback_data="settings",
            ),
            InlineKeyboardButton(
                "❔ HELP",
                callback_data="help",
            ),
        ],
    ])


def back_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "‹ BACK",
                callback_data="home",
            )
        ]
    ])


# ============================================================
# PREMIUM TEXT
# ============================================================

def welcome_text(group_name):
    return (
        f"╭━━━━━━━━━━━━━━━━━━━━╮\n"
        f"       ✦ **{group_name}** ✦\n"
        f"╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
        f"> **ADMIN MANAGEMENT CENTER**\n\n"
        f"Welcome 👋\n"
        f"ဒီ Bot က Group Admin တွေရဲ့\n\n"
        f"  ◈ 🗓️ Schedule\n"
        f"  ◈ ⏰ Duty Time\n"
        f"  ◈ 🔔 Reminder\n"
        f"  ◈ 👤 Attendance\n"
        f"  ◈ 👥 Member Activity\n"
        f"  ◈ 📊 Reports\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"**Quick Action**\n"
        f"အောက်က Menu ကနေ ရွေးချယ်ပါ။"
    )


# ============================================================
# /START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
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
        group_name = chat.title or "THIS GROUP"
    else:
        group_name = "LUXURY NEXUS"

    await update.message.reply_text(
        welcome_text(group_name),
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )


# ============================================================
# TIME PARSER
# ============================================================

def parse_one_time(value):
    value = value.strip().upper()

    formats = [
        "%H:%M",
        "%H",
        "%I:%M %p",
        "%I %p",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(
                value,
                fmt,
            ).time()
        except ValueError:
            pass

    return None


def parse_time_range(text):
    text = text.strip()

    parts = re.split(
        r"\s*-\s*",
        text,
        maxsplit=1,
    )

    if len(parts) != 2:
        return None

    start = parse_one_time(parts[0])
    end = parse_one_time(parts[1])

    if not start or not end:
        return None

    return start, end


# ============================================================
# SCHEDULE INPUT
# ============================================================

async def handle_schedule_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if update.effective_chat.type != "private":
        return

    user = update.effective_user

    register_user(user)

    text = update.message.text.strip()

    pattern = (
        r"^(today|tomorrow)"
        r"\s+(.+?)"
        r"\s+(day|night)"
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

    parsed = parse_time_range(
        time_text
    )

    if not parsed:
        await update.message.reply_text(
            "╭━━━━━━━━━━━━━━━━━━━━╮\n"
            "        ⚠️ **INVALID TIME**\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "ဒီလို format နဲ့ ပို့ပါ 👇\n\n"
            "> `Today 12:15-1:00 day`\n"
            "> `Tomorrow 8:00-12:00 night`\n\n"
            "24-hour format လည်းရပါတယ်။"
        )
        return

    start_t, end_t = parsed

    groups = await get_user_groups(
        context,
        user.id,
    )

    if not groups:
        await update.message.reply_text(
            "⛔ **ADMIN GROUP မတွေ့ပါဘူး။**\n\n"
            "Bot ကို Group ထဲမှာ Admin အဖြစ်ထည့်ပြီး\n"
            "`/checkadmin` ကို တစ်ခါ run လုပ်ပါ။",
            parse_mode="Markdown",
        )
        return

    if day_word == "today":
        target_date = today_local()
    else:
        target_date = today_local() + timedelta(days=1)

    if len(groups) == 1:

        group = groups[0]

        schedule_id = save_schedule(
            group["chat_id"],
            user.id,
            target_date,
            start_t,
            end_t,
            shift,
            note,
        )

        await schedule_jobs(
            context,
            schedule_id,
        )

        await update.message.reply_text(
            schedule_saved_text(
                group["title"],
                user,
                target_date,
                start_t,
                end_t,
                shift,
                note,
            ),
            parse_mode="Markdown",
        )

        return

    context.user_data["pending_schedule"] = {
        "admin_id": user.id,
        "day": day_word,
        "start": start_t.strftime("%H:%M"),
        "end": end_t.strftime("%H:%M"),
        "shift": shift,
        "note": note,
    }

    buttons = []

    for group in groups:
        buttons.append([
            InlineKeyboardButton(
                f"⌂ {group['title'][:45]}",
                callback_data=f"sg:{group['chat_id']}",
            )
        ])

    await update.message.reply_text(
        "╭━━━━━━━━━━━━━━━━━━━━╮\n"
        "       ⌂ **SELECT GROUP**\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
        "ဒီ Schedule ကို ဘယ် Group အတွက် မှတ်မလဲ?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


def schedule_saved_text(
    group_title,
    user,
    target_date,
    start_t,
    end_t,
    shift,
    note,
):

    shift_icon = "☀️" if shift == "day" else "🌙"

    return (
        "╭━━━━━━━━━━━━━━━━━━━━╮\n"
        "       ✦ **SCHEDULE SAVED**\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
        f"> **GROUP**\n"
        f"> {group_title}\n\n"
        f"👤 **ADMIN**\n"
        f"{user.full_name}\n\n"
        f"🗓️ **DATE**\n"
        f"{format_date(target_date)}\n\n"
        f"⏰ **DUTY TIME**\n"
        f"{start_t.strftime('%I:%M %p').lstrip('0')} "
        f"→ "
        f"{end_t.strftime('%I:%M %p').lstrip('0')}\n\n"
        f"{shift_icon} **{shift.upper()} SHIFT**\n\n"
        f"📝 **NOTE**\n"
        f"{note or '—'}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔔 **Reminder: 15 minutes before**"
    )


# ============================================================
# SAVE SCHEDULE
# ============================================================

def save_schedule(
    chat_id,
    admin_id,
    schedule_date,
    start_t,
    end_t,
    shift,
    note="",
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
        schedule_date.isoformat(),
        start_t.strftime("%H:%M"),
        end_t.strftime("%H:%M"),
        shift,
        note,
        now_iso(),
    ))

    schedule_id = cur.lastrowid

    conn.commit()
    conn.close()

    return schedule_id


# ============================================================
# GET USER GROUPS
# ============================================================

async def get_user_groups(
    context,
    user_id,
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


# ============================================================
# SCHEDULE JOBS
# ============================================================

async def schedule_jobs(
    context,
    schedule_id,
):

    conn = get_db()

    row = conn.execute("""
        SELECT *
        FROM schedules
        WHERE id = ?
    """, (
        schedule_id,
    )).fetchone()

    conn.close()

    if not row:
        return

    target_date = date.fromisoformat(
        row["schedule_date"]
    )

    start_t = datetime.strptime(
        row["start_time"],
        "%H:%M",
    ).time()

    end_t = datetime.strptime(
        row["end_time"],
        "%H:%M",
    ).time()

    start_dt = datetime.combine(
        target_date,
        start_t,
        TZ,
    )

    end_dt = datetime.combine(
        target_date,
        end_t,
        TZ,
    )

    if end_dt <= start_dt:
        end_dt += timedelta(days=1)

    reminder_dt = (
        start_dt
        - timedelta(
            minutes=REMINDER_MINUTES
        )
    )

    now = now_local()

    if reminder_dt > now:
        context.job_queue.run_once(
            reminder_job,
            when=reminder_dt,
            data={"schedule_id": schedule_id},
            name=f"reminder_{schedule_id}",
        )

    if start_dt > now:
        context.job_queue.run_once(
            start_job,
            when=start_dt,
            data={"schedule_id": schedule_id},
            name=f"start_{schedule_id}",
        )

    if end_dt > now:
        context.job_queue.run_once(
            end_job,
            when=end_dt,
            data={"schedule_id": schedule_id},
            name=f"end_{schedule_id}",
        )


async def restore_jobs(
    context,
):

    conn = get_db()

    rows = conn.execute("""
        SELECT id
        FROM schedules
        WHERE schedule_date >= ?
    """, (
        today_local().isoformat(),
    )).fetchall()

    conn.close()

    for row in rows:
        try:
            await schedule_jobs(
                context,
                row["id"],
            )
        except Exception as e:
            logger.error(
                "Job restore error: %s",
                e,
            )


# ============================================================
# JOB DATA
# ============================================================

def get_schedule(schedule_id):

    conn = get_db()

    row = conn.execute("""
        SELECT
            s.*,
            g.title,
            u.full_name,
            u.username
        FROM schedules s

        LEFT JOIN groups g
            ON s.chat_id = g.chat_id

        LEFT JOIN users u
            ON s.admin_id = u.user_id

        WHERE s.id = ?
    """, (
        schedule_id,
    )).fetchone()

    conn.close()

    return row


# ============================================================
# REMINDER
# ============================================================

async def reminder_job(
    context: ContextTypes.DEFAULT_TYPE,
):

    schedule_id = context.job.data["schedule_id"]

    row = get_schedule(
        schedule_id
    )

    if not row:
        return

    try:

        await context.bot.send_message(
            chat_id=row["chat_id"],
            text=(
                "╭━━━━━━━━━━━━━━━━━━━━╮\n"
                "        🔔 **DUTY REMINDER**\n"
                "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
                f"> 👤 **ADMIN**\n"
                f"> {row['full_name']}\n\n"
                f"⏰ **Duty starts in 15 minutes**\n"
                f"{row['start_time']} → {row['end_time']}\n\n"
                f"🏷️ {row['shift'].upper()} SHIFT\n\n"
                f"📝 {row['note'] or '—'}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"⚡ Please be ready."
            ),
            parse_mode="Markdown",
        )

    except Exception as e:
        logger.error(
            "Reminder error: %s",
            e,
        )


# ============================================================
# START JOB
# ============================================================

async def start_job(
    context: ContextTypes.DEFAULT_TYPE,
):

    schedule_id = context.job.data["schedule_id"]

    row = get_schedule(
        schedule_id
    )

    if not row:
        return

    try:

        await context.bot.send_message(
            chat_id=row["chat_id"],
            text=(
                "╭━━━━━━━━━━━━━━━━━━━━╮\n"
                "        🟢 **DUTY STARTED**\n"
                "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
                f"👤 **Admin:** {row['full_name']}\n"
                f"⏰ **Time:** {row['start_time']} → {row['end_time']}\n"
                f"🏷️ **Shift:** {row['shift'].upper()}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🟢 Duty is now active."
            ),
            parse_mode="Markdown",
        )

        conn = get_db()

        conn.execute("""
            INSERT INTO attendance
            (
                schedule_id,
                admin_id,
                chat_id,
                status,
                checked_at
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            schedule_id,
            row["admin_id"],
            row["chat_id"],
            "started",
            now_iso(),
        ))

        conn.commit()
        conn.close()

    except Exception as e:
        logger.error(
            "Start job error: %s",
            e,
        )


# ============================================================
# END JOB
# ============================================================

async def end_job(
    context: ContextTypes.DEFAULT_TYPE,
):

    schedule_id = context.job.data["schedule_id"]

    row = get_schedule(
        schedule_id
    )

    if not row:
        return

    try:

        await context.bot.send_message(
            chat_id=row["chat_id"],
            text=(
                "╭━━━━━━━━━━━━━━━━━━━━╮\n"
                "         🔴 **DUTY ENDED**\n"
                "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
                f"👤 **Admin:** {row['full_name']}\n"
                f"⏰ **Finished:** {row['end_time']}\n"
                f"🏷️ **Shift:** {row['shift'].upper()}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"✓ Duty session completed."
            ),
            parse_mode="Markdown",
        )

    except Exception as e:
        logger.error(
            "End job error: %s",
            e,
        )


# ============================================================
# TODAY SCHEDULE
# ============================================================

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
            "⛔ **Admin Group မတွေ့ပါဘူး။**",
            parse_mode="Markdown",
            reply_markup=back_keyboard(),
        )

        return

    chat_ids = [
        group["chat_id"]
        for group in groups
    ]

    placeholders = ",".join(
        "?"
        for _ in chat_ids
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

        ORDER BY
            s.start_time
        """,
        (
            *chat_ids,
            target_date.isoformat(),
        ),
    ).fetchall()

    conn.close()

    if not rows:

        await query.edit_message_text(
            "╭━━━━━━━━━━━━━━━━━━━━╮\n"
            "        ◈ **SCHEDULE**\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
            f"🗓️ **{format_date(target_date)}**\n\n"
            "> No schedule has been added yet.",
            parse_mode="Markdown",
            reply_markup=back_keyboard(),
        )

        return

    lines = [
        "╭━━━━━━━━━━━━━━━━━━━━╮",
        "        ◈ **SCHEDULE**",
        "╰━━━━━━━━━━━━━━━━━━━━╯",
        "",
        f"🗓️ **{format_date(target_date)}**",
        "",
    ]

    for row in rows:

        icon = (
            "☀️"
            if row["shift"] == "day"
            else "🌙"
        )

        lines.extend([
            "╭────────────────────",
            f"│ {icon} **{row['full_name']}**",
            f"│ ⏰ {row['start_time']} → {row['end_time']}",
            f"│ 🏠 {row['title']}",
            f"│ 📝 {row['note'] or '—'}",
            "╰────────────────────",
            "",
        ])

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=back_keyboard(),
    )


# ============================================================
# MY SCHEDULE
# ============================================================

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

        LIMIT 30
    """, (
        user_id,
        today_local().isoformat(),
    )).fetchall()

    conn.close()

    if not rows:

        await query.edit_message_text(
            "╭━━━━━━━━━━━━━━━━━━━━╮\n"
            "        ◈ **MY SCHEDULE**\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "> Schedule မရှိသေးပါဘူး။",
            parse_mode="Markdown",
            reply_markup=back_keyboard(),
        )

        return

    lines = [
        "╭━━━━━━━━━━━━━━━━━━━━╮",
        "        ◈ **MY SCHEDULE**",
        "╰━━━━━━━━━━━━━━━━━━━━╯",
        "",
    ]

    for row in rows:

        icon = (
            "☀️"
            if row["shift"] == "day"
            else "🌙"
        )

        lines.extend([
            f"**{format_date(date.fromisoformat(row['schedule_date']))}**",
            f"{icon} `{row['start_time']} → {row['end_time']}`",
            f"🏠 {row['title']}",
            f"📝 {row['note'] or '—'}",
            "",
        ])

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=back_keyboard(),
    )


# ============================================================
# CANCEL SCHEDULE
# ============================================================

async def cancel_schedule(
    update,
    context,
):

    if not await require_group_admin(
        update,
        context,
    ):
        return

    user = update.effective_user
    chat = update.effective_chat

    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM schedules
        WHERE chat_id = ?
        AND schedule_date >= ?
        ORDER BY schedule_date, start_time
        LIMIT 20
    """, (
        chat.id,
        today_local().isoformat(),
    )).fetchall()

    conn.close()

    if not rows:

        await update.message.reply_text(
            "📋 ဒီ Group မှာ Cancel လုပ်လို့ရမယ့် Schedule မရှိပါဘူး။"
        )
        return

    buttons = []

    for row in rows:

        buttons.append([
            InlineKeyboardButton(
                f"❌ {row['schedule_date']} "
                f"{row['start_time']} "
                f"→ {row['end_time']}",
                callback_data=f"cancel:{row['id']}",
            )
        ])

    await update.message.reply_text(
        "╭━━━━━━━━━━━━━━━━━━━━╮\n"
        "       ❌ **CANCEL SCHEDULE**\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
        "ဖျက်ချင်တဲ့ Schedule ကို ရွေးပါ။",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


# ============================================================
# ADMINS LIST
# ============================================================

async def show_admins(
    query,
    context,
):

    groups = await get_user_groups(
        context,
        query.from_user.id,
    )

    if not groups:

        await query.edit_message_text(
            "⛔ Admin Group မတွေ့ပါဘူး။",
            reply_markup=back_keyboard(),
        )

        return

    lines = [
        "╭━━━━━━━━━━━━━━━━━━━━╮",
        "          ♛ **ADMINS**",
        "╰━━━━━━━━━━━━━━━━━━━━╯",
        "",
    ]

    for group in groups:

        lines.append(
            f"🏠 **{group['title']}**"
        )

        try:

            admins = await context.bot.get_chat_administrators(
                group["chat_id"]
            )

            for member in admins:

                name = user_display(
                    member.user
                )

                role = (
                    "OWNER"
                    if member.status == ChatMemberStatus.OWNER
                    else "ADMIN"
                )

                lines.append(
                    f"  ♛ {name} — `{role}`"
                )

        except Exception as e:

            logger.error(
                "Admin list error: %s",
                e,
            )

        lines.append("")

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=back_keyboard(),
    )


# ============================================================
# REPORT
# ============================================================

async def report_command(
    update,
    context,
):

    if not await require_group_admin(
        update,
        context,
    ):
        return

    chat = update.effective_chat

    start_date = today_local()

    end_date = (
        start_date
        + timedelta(days=7)
    )

    conn = get_db()

    schedules = conn.execute("""
        SELECT
            s.*,
            u.full_name
        FROM schedules s

        LEFT JOIN users u
            ON s.admin_id = u.user_id

        WHERE s.chat_id = ?

        AND s.schedule_date >= ?

        AND s.schedule_date < ?

        ORDER BY
            s.schedule_date,
            s.start_time
    """, (
        chat.id,
        start_date.isoformat(),
        end_date.isoformat(),
    )).fetchall()

    members = conn.execute("""
        SELECT COUNT(*) AS total
        FROM member_events
        WHERE chat_id = ?
        AND created_at >= ?
    """, (
        chat.id,
        (
            datetime.combine(
                start_date,
                time.min,
                TZ,
            ).isoformat()
        ),
    )).fetchone()

    conn.close()

    lines = [
        "╭━━━━━━━━━━━━━━━━━━━━╮",
        "        ▣ **WEEKLY REPORT**",
        "╰━━━━━━━━━━━━━━━━━━━━╯",
        "",
        f"🏠 **{chat.title}**",
        f"🗓️ {format_date(start_date)}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"📅 **Schedules:** {len(schedules)}",
        f"👥 **Member Events:** {members['total']}",
        "",
    ]

    if schedules:

        for row in schedules:

            lines.append(
                f"• {row['schedule_date']} "
                f"| {row['start_time']} → "
                f"{row['end_time']}"
            )

            lines.append(
                f"  👤 {row['full_name']}"
            )

            lines.append("")

    else:

        lines.append(
            "> No schedule data."
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
    )


# ============================================================
# ADD ADMIN
# ============================================================

async def add_admin(
    update,
    context,
):

    if not await require_group_admin(
        update,
        context,
    ):
        return

    if not context.args:

        await update.message.reply_text(
            "အသုံးပြုပုံ:\n\n"
            "`/addadmin USER_ID`",
            parse_mode="Markdown",
        )
        return

    try:

        user_id = int(
            context.args[0]
        )

    except ValueError:

        await update.message.reply_text(
            "❌ User ID က နံပါတ်ဖြစ်ရပါမယ်။"
        )
        return

    chat = update.effective_chat

    conn = get_db()

    conn.execute("""
        INSERT OR REPLACE INTO group_admins
        (
            chat_id,
            user_id,
            role,
            note,
            added_at
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        chat.id,
        user_id,
        "admin",
        "",
        now_iso(),
    ))

    conn.commit()
    conn.close()

    await update.message.reply_text(
        "✅ Admin record ထည့်ပြီးပါပြီ။\n\n"
        f"🆔 `{user_id}`",
        parse_mode="Markdown",
    )


# ============================================================
# REMOVE ADMIN
# ============================================================

async def remove_admin(
    update,
    context,
):

    if not await require_group_admin(
        update,
        context,
    ):
        return

    if not context.args:

        await update.message.reply_text(
            "အသုံးပြုပုံ:\n\n"
            "`/removeadmin USER_ID`",
            parse_mode="Markdown",
        )
        return

    try:

        user_id = int(
            context.args[0]
        )

    except ValueError:

        await update.message.reply_text(
            "❌ User ID မမှန်ပါဘူး။"
        )
        return

    chat = update.effective_chat

    conn = get_db()

    conn.execute("""
        DELETE FROM group_admins
        WHERE chat_id = ?
        AND user_id = ?
    """, (
        chat.id,
        user_id,
    ))

    conn.commit()
    conn.close()

    await update.message.reply_text(
        "🗑️ Admin record ဖယ်ရှားပြီးပါပြီ။"
    )


# ============================================================
# MEMBER JOIN / LEAVE
# ============================================================

async def member_update(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat_member = update.chat_member

    if not chat_member:
        return

    chat = chat_member.chat

    old_status = chat_member.old_chat_member.status
    new_status = chat_member.new_chat_member.status

    user = chat_member.new_chat_member.user

    register_group(
        chat.id,
        chat.title,
    )

    event = None

    if new_status in (
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.RESTRICTED,
    ) and old_status in (
        ChatMemberStatus.LEFT,
        ChatMemberStatus.KICKED,
    ):
        event = "join"

    elif new_status in (
        ChatMemberStatus.LEFT,
        ChatMemberStatus.KICKED,
    ) and old_status in (
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.RESTRICTED,
        ChatMemberStatus.ADMINISTRATOR,
    ):
        event = "leave"

    if not event:
        return

    register_user(user)

    conn = get_db()

    conn.execute("""
        INSERT INTO member_events
        (
            chat_id,
            user_id,
            username,
            full_name,
            event,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        chat.id,
        user.id,
        user.username or "",
        user.full_name or "",
        event,
        now_iso(),
    ))

    conn.commit()
    conn.close()


# ============================================================
# /GROUPID
# ============================================================

async def groupid(
    update,
    context,
):

    chat = update.effective_chat

    if chat.type not in (
        "group",
        "supergroup",
    ):

        await update.message.reply_text(
            "ဒီ command ကို Group ထဲမှာ သုံးပါ။"
        )

        return

    register_group(
        chat.id,
        chat.title,
    )

    await update.message.reply_text(
        "╭━━━━━━━━━━━━━━━━━━━━╮\n"
        "          ◈ **GROUP INFO**\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
        f"🏠 **Group:** {chat.title}\n\n"
        f"🆔 **ID:** `{chat.id}`",
        parse_mode="Markdown",
    )


# ============================================================
# /CHECKADMIN
# ============================================================

async def checkadmin(
    update,
    context,
):

    chat = update.effective_chat
    user = update.effective_user

    if chat.type not in (
        "group",
        "supergroup",
    ):

        await update.message.reply_text(
            "ဒီ command ကို Group ထဲမှာ သုံးပါ။"
        )

        return

    admin = await is_group_admin(
        context,
        chat.id,
        user.id,
    )

    if admin:

        register_group(
            chat.id,
            chat.title,
        )

        await update.message.reply_text(
            "╭━━━━━━━━━━━━━━━━━━━━╮\n"
            "        ♛ **ADMIN VERIFIED**\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "✅ မင်းက ဒီ Group ရဲ့ Admin ဖြစ်ပါတယ်။\n\n"
            "🔐 Permission: **ACTIVE**"
        )

    else:

        await update.message.reply_text(
            "╭━━━━━━━━━━━━━━━━━━━━╮\n"
            "        ⛔ **ACCESS DENIED**\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "ဒီ Group ရဲ့ Admin မဟုတ်ပါဘူး။"
        )


# ============================================================
# /TODAY
# ============================================================

async def today_command(
    update,
    context,
):

    if not await require_group_admin(
        update,
        context,
    ):
        return

    chat = update.effective_chat

    conn = get_db()

    rows = conn.execute("""
        SELECT
            s.*,
            u.full_name
        FROM schedules s

        LEFT JOIN users u
            ON s.admin_id = u.user_id

        WHERE s.chat_id = ?
        AND s.schedule_date = ?

        ORDER BY s.start_time
    """, (
        chat.id,
        today_local().isoformat(),
    )).fetchall()

    conn.close()

    if not rows:

        await update.message.reply_text(
            "╭━━━━━━━━━━━━━━━━━━━━╮\n"
            "       ✦ **TODAY**\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "> Schedule မရှိသေးပါဘူး။"
        )

        return

    lines = [
        "╭━━━━━━━━━━━━━━━━━━━━╮",
        "       ✦ **TODAY DUTY**",
        "╰━━━━━━━━━━━━━━━━━━━━╯",
        "",
    ]

    for row in rows:

        icon = (
            "☀️"
            if row["shift"] == "day"
            else "🌙"
        )

        lines.extend([
            f"{icon} **{row['full_name']}**",
            f"⏰ {row['start_time']} → {row['end_time']}",
            f"📝 {row['note'] or '—'}",
            "",
        ])

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
    )


# ============================================================
# /TOMORROW
# ============================================================

async def tomorrow_command(
    update,
    context,
):

    if not await require_group_admin(
        update,
        context,
    ):
        return

    chat = update.effective_chat

    target = (
        today_local()
        + timedelta(days=1)
    )

    conn = get_db()

    rows = conn.execute("""
        SELECT
            s.*,
            u.full_name
        FROM schedules s

        LEFT JOIN users u
            ON s.admin_id = u.user_id

        WHERE s.chat_id = ?
        AND s.schedule_date = ?

        ORDER BY s.start_time
    """, (
        chat.id,
        target.isoformat(),
    )).fetchall()

    conn.close()

    if not rows:

        await update.message.reply_text(
            "📅 **Tomorrow**\n\n"
            "> Schedule မရှိသေးပါဘူး။",
            parse_mode="Markdown",
        )

        return

    lines = [
        "╭━━━━━━━━━━━━━━━━━━━━╮",
        "      ✦ **TOMORROW DUTY**",
        "╰━━━━━━━━━━━━━━━━━━━━╯",
        "",
    ]

    for row in rows:

        icon = (
            "☀️"
            if row["shift"] == "day"
            else "🌙"
        )

        lines.extend([
            f"{icon} **{row['full_name']}**",
            f"⏰ {row['start_time']} → {row['end_time']}",
            f"📝 {row['note'] or '—'}",
            "",
        ])

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
    )


# ============================================================
# OWNER BROADCAST
# ============================================================

async def broadcast_command(
    update,
    context,
):

    user = update.effective_user

    if user.id != OWNER_ID:

        await update.message.reply_text(
            "⛔ Owner only."
        )

        return

    if not update.message.reply_to_message:

        await update.message.reply_text(
            "Broadcast လုပ်ချင်တဲ့ message ကို "
            "Reply လုပ်ပြီး `/broadcast` ပို့ပါ။"
        )

        return

    source = update.message.reply_to_message

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

    sent = 0
    failed = 0

    targets = set()

    for row in users:
        targets.add(row["user_id"])

    for row in groups:
        targets.add(row["chat_id"])

    for chat_id in targets:

        try:

            await source.copy(
                chat_id=chat_id,
            )

            sent += 1

        except Exception as e:

            logger.warning(
                "Broadcast failed %s: %s",
                chat_id,
                e,
            )

            failed += 1

    await update.message.reply_text(
        "╭━━━━━━━━━━━━━━━━━━━━╮\n"
        "        📢 **BROADCAST DONE**\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
        f"🟢 Sent: **{sent}**\n"
        f"🔴 Failed: **{failed}**",
        parse_mode="Markdown",
    )


# ============================================================
# CALLBACKS
# ============================================================

async def callbacks(
    update,
    context,
):

    query = update.callback_query

    await query.answer()

    data = query.data

    # HOME
    if data == "home":

        await query.edit_message_text(
            welcome_text(
                "LUXURY NEXUS"
            ),
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )

        return

    # TODAY
    if data == "today":

        groups = await get_user_groups(
            context,
            query.from_user.id,
        )

        if groups:

            await show_schedule(
                query,
                context,
                today_local(),
            )

        else:

            await query.edit_message_text(
                "⛔ Admin Group မတွေ့ပါဘူး။",
                reply_markup=back_keyboard(),
            )

        return

    # TOMORROW
    if data == "tomorrow":

        await show_schedule(
            query,
            context,
            today_local()
            + timedelta(days=1),
        )

        return

    # MINE
    if data == "mine":

        await show_my_schedule(
            query,
            context,
        )

        return

    # ADD
    if data == "add":

        await query.edit_message_text(
            "╭━━━━━━━━━━━━━━━━━━━━╮\n"
            "       ＋ **ADD SCHEDULE**\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "DM မှာ ဒီလိုပို့ပါ 👇\n\n"
            "> `Today 12:15-1:00 day`\n\n"
            "> `Tomorrow 8:00-12:00 night`\n\n"
            "📝 Note ထည့်ချင်ရင် —\n"
            "> `Today 12:15-1:00 day morning duty`",
            parse_mode="Markdown",
            reply_markup=back_keyboard(),
        )

        return

    # ADMINS
    if data == "admins":

        await show_admins(
            query,
            context,
        )

        return

    # REPORTS
    if data == "reports":

        await query.edit_message_text(
            "╭━━━━━━━━━━━━━━━━━━━━╮\n"
            "        ▣ **REPORTS**\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "Group ထဲမှာ\n\n"
            "`/report`\n\n"
            "ကိုသုံးပြီး Weekly Report ကြည့်နိုင်ပါတယ်။",
            parse_mode="Markdown",
            reply_markup=back_keyboard(),
        )

        return

    # SETTINGS
    if data == "settings":

        await query.edit_message_text(
            "╭━━━━━━━━━━━━━━━━━━━━╮\n"
            "        ⚙ **SETTINGS**\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "🕐 Timezone: **Myanmar Time**\n"
            "🔔 Reminder: **15 minutes**\n"
            "👥 Member Tracking: **ON**\n"
            "💾 Database: **ON**",
            parse_mode="Markdown",
            reply_markup=back_keyboard(),
        )

        return

    # HELP
    if data == "help":

        await query.edit_message_text(
            "╭━━━━━━━━━━━━━━━━━━━━╮\n"
            "         ❔ **HELP**\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "🗓️ Schedule format:\n\n"
            "> `Today 12:15-1:00 day`\n"
            "> `Tomorrow 8:00-12:00 night`\n\n"
            "Commands:\n\n"
            "• `/today`\n"
            "• `/tomorrow`\n"
            "• `/report`\n"
            "• `/cancelschedule`\n"
            "• `/checkadmin`\n"
            "• `/groupid`\n\n"
            "🔔 15-min Reminder\n"
            "🟢 Start Notification\n"
            "🔴 End Notification",
            parse_mode="Markdown",
            reply_markup=back_keyboard(),
        )

        return

    # SELECT GROUP
    if data.startswith("sg:"):

        chat_id = int(
            data.split(
                ":",
                1,
            )[1]
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

        if not await is_group_admin(
            context,
            chat_id,
            user_id,
        ):

            await query.edit_message_text(
                "⛔ ဒီ Group ရဲ့ Admin မဟုတ်တော့ပါဘူး။"
            )

            return

        target_date = (
            today_local()
            if pending["day"] == "today"
            else today_local()
            + timedelta(days=1)
        )

        start_t = datetime.strptime(
            pending["start"],
            "%H:%M",
        ).time()

        end_t = datetime.strptime(
            pending["end"],
            "%H:%M",
        ).time()

        schedule_id = save_schedule(
            chat_id,
            user_id,
            target_date,
            start_t,
            end_t,
            pending["shift"],
            pending["note"],
        )

        await schedule_jobs(
            context,
            schedule_id,
        )

        conn = get_db()

        row = conn.execute("""
            SELECT title
            FROM groups
            WHERE chat_id = ?
        """, (
            chat_id,
        )).fetchone()

        conn.close()

        title = (
            row["title"]
            if row
            else "Group"
        )

        await query.edit_message_text(
            schedule_saved_text(
                title,
                query.from_user,
                target_date,
                start_t,
                end_t,
                pending["shift"],
                pending["note"],
            ),
            parse_mode="Markdown",
        )

        context.user_data.pop(
            "pending_schedule",
            None,
        )

        return

    # CANCEL
    if data.startswith("cancel:"):

        schedule_id = int(
            data.split(
                ":",
                1,
            )[1]
        )

        row = get_schedule(
            schedule_id
        )

        if not row:

            await query.edit_message_text(
                "❌ Schedule မတွေ့တော့ပါဘူး။"
            )

            return

        if not await is_group_admin(
            context,
            row["chat_id"],
            query.from_user.id,
        ):

            await query.edit_message_text(
                "⛔ Permission denied."
            )

            return

        conn = get_db()

        conn.execute("""
            DELETE FROM schedules
            WHERE id = ?
        """, (
            schedule_id,
        ))

        conn.commit()
        conn.close()

        # Remove jobs
        for job_name in (
            f"reminder_{schedule_id}",
            f"start_{schedule_id}",
            f"end_{schedule_id}",
        ):

            jobs = context.job_queue.get_jobs_by_name(
                job_name
            )

            for job in jobs:
                job.schedule_removal()

        await query.edit_message_text(
            "╭━━━━━━━━━━━━━━━━━━━━╮\n"
            "       ✓ **SCHEDULE CANCELLED**\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "Schedule ကို ဖျက်ပြီးပါပြီ။"
        )

        return


# ============================================================
# CLEANUP
# ============================================================

async def cleanup_job(
    context,
):

    cutoff = (
        today_local()
        - timedelta(days=30)
    )

    conn = get_db()

    conn.execute("""
        DELETE FROM schedules
        WHERE schedule_date < ?
    """, (
        cutoff.isoformat(),
    ))

    conn.execute("""
        DELETE FROM member_events
        WHERE created_at < ?
    """, (
        datetime.combine(
            cutoff,
            time.min,
            TZ,
        ).isoformat(),
    ))

    conn.commit()
    conn.close()

    logger.info(
        "Old data cleanup completed."
    )


# ============================================================
# POST INIT
# ============================================================

async def post_init(
    application,
):

    await restore_jobs(
        application,
    )

    application.job_queue.run_repeating(
        cleanup_job,
        interval=86400,
        first=60,
        name="daily_cleanup",
    )

    logger.info(
        "Schedule jobs restored."
    )


# ============================================================
# ERROR
# ============================================================

async def error_handler(
    update,
    context,
):

    logger.error(
        "Update error: %s",
        context.error,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not TOKEN:

        raise RuntimeError(
            "BOT_TOKEN is missing."
        )

    init_db()

    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    # Commands
    app.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    app.add_handler(
        CommandHandler(
            "today",
            today_command,
        )
    )

    app.add_handler(
        CommandHandler(
            "tomorrow",
            tomorrow_command,
        )
    )

    app.add_handler(
        CommandHandler(
            "report",
            report_command,
        )
    )

    app.add_handler(
        CommandHandler(
            "cancelschedule",
            cancel_schedule,
        )
    )

    app.add_handler(
        CommandHandler(
            "addadmin",
            add_admin,
        )
    )

    app.add_handler(
        CommandHandler(
            "removeadmin",
            remove_admin,
        )
    )

    app.add_handler(
        CommandHandler(
            "groupid",
            groupid,
        )
    )

    app.add_handler(
        CommandHandler(
            "checkadmin",
            checkadmin,
        )
    )

    app.add_handler(
        CommandHandler(
            "broadcast",
            broadcast_command,
        )
    )

    # Buttons
    app.add_handler(
        CallbackQueryHandler(
            callbacks,
        )
    )

    # Member join / leave
    app.add_handler(
        ChatMemberHandler(
            member_update,
            ChatMemberHandler.CHAT_MEMBER,
        )
    )

    # Schedule text
    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            handle_schedule_text,
        )
    )

    app.add_error_handler(
        error_handler
    )

    print(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        flush=True,
    )

    print(
        "🤖 GROUP ADMIN MANAGEMENT BOT",
        flush=True,
    )

    print(
        "✦ Premium UI Edition",
        flush=True,
    )

    print(
        "🕐 Myanmar Time",
        flush=True,
    )

    print(
        "🔔 Reminder System: ON",
        flush=True,
    )

    print(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        flush=True,
    )

    app.run_polling()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
