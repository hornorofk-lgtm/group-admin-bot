"""
Telegram Quiz Bot (ညဏ်စမ်းဘော့)
- မေးခွန်း random မေးမယ်၊ အဖြေ ၄ ခု (အစိမ်းရောင် button)
- ၁၀ စက္ကန့်အတွင်း အဖြေမှန်ကို အရင်နှိပ်သူ အမှတ်ရမယ်
- Group အားလုံးမှာ ဆော့လို့ရ၊ Global leaderboard၊ Owner broadcast
"""
import asyncio
import glob
import html
import json
import logging
import os
import random

import asyncpg
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
log = logging.getLogger("quizbot")

# ---------------------------------------------------------------- settings
BOT_TOKEN = os.environ["BOT_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
GROUP_LINK = os.environ.get("GROUP_LINK", "")
OWNER_LINK = os.environ.get("OWNER_LINK", "")
SUPPORT_LINK = os.environ.get("SUPPORT_LINK", "")
# button အစိမ်းရောင် မပေါ်ရင် Railway Variables မှာ GREEN_EMOJI=1 ထည့်ပါ
GREEN_EMOJI = os.environ.get("GREEN_EMOJI", "0") == "1"

ANSWER_SECONDS = 10  # အဖြေရွေးချိန်
PAUSE_SECONDS = 3  # မေးခွန်းတစ်ခုနဲ့တစ်ခုကြား နားချိန်
MAX_IDLE_ROUNDS = 3  # ဆက်တိုက် ဘယ်သူမှမဖြေရင် အလိုအလျောက် ရပ်မယ်

INTRO_TEXT = (
    "👋 <b>မင်္ဂလာပါ!</b>\n\n"
    "🧠 ဒီဘော့က <b>ဉာဏ်စမ်းမေးခွန်းဘော့</b> ပါ။ "
    "မေးခွန်းတစ်ခုစီကို အဖြေ ၄ ခုထဲက ရွေးဖြေရမယ်၊ "
    f"<b>{ANSWER_SECONDS} စက္ကန့်</b>အတွင်း အဖြေမှန်ကို အရင်နှိပ်သူက အမှတ်ရမယ်။\n\n"
    "▶️ /quiz - ဂိမ်းစမယ်\n"
    "⏹ /stop - ဂိမ်းရပ်မယ်\n"
    "🏅 /score - ကိုယ့်အမှတ်ကြည့်မယ်\n"
    "🌍 /top - Global leaderboard\n\n"
    "➕ Group ထဲ ထည့်ပြီး သူငယ်ချင်းတွေနဲ့ ယှဉ်ပြိုင်ကြည့်ပါ!"
)


# ---------------------------------------------------------------- questions
def load_questions():
    base = os.path.dirname(os.path.abspath(__file__))
    questions = []
    for path in sorted(glob.glob(os.path.join(base, "questions_*.json"))):
        with open(path, encoding="utf-8") as f:
            questions.extend(json.load(f))
    return questions


QUESTIONS = load_questions()
log.info("Loaded %d questions", len(QUESTIONS))

DECKS = {}  # chat_id -> မေးမယ့် မေးခွန်း အစီအစဉ် (random)
GAMES = {}  # chat_id -> လက်ရှိ ဂိမ်းအခြေအနေ


def pick_question(chat_id):
    deck = DECKS.get(chat_id)
    if not deck:
        deck = list(range(len(QUESTIONS)))
        random.shuffle(deck)
        DECKS[chat_id] = deck
    q = QUESTIONS[deck.pop()]
    options = list(q["o"])
    correct_text = options[0]  # အဖြေမှန်က အမြဲ ပထမဆုံးရှိတယ်
    random.shuffle(options)
    return q["q"], options, options.index(correct_text)


# ---------------------------------------------------------------- helpers
def green(text, **kwargs):
    """အစိမ်းရောင် button (Telegram button style: success)"""
    if GREEN_EMOJI:
        text = "🟢 " + text
    return InlineKeyboardButton(text, api_kwargs={"style": "success"}, **kwargs)


def mention(user):
    name = html.escape(user.first_name or "Player")
    return f'<a href="tg://user?id={user.id}">{name}</a>'


def intro_keyboard(bot_username):
    rows = []
    if GROUP_LINK:
        rows.append([green("👥 Group", url=GROUP_LINK)])
    if OWNER_LINK:
        rows.append([green("👤 Owner", url=OWNER_LINK)])
    if SUPPORT_LINK:
        rows.append([green("💬 Support Channel", url=SUPPORT_LINK)])
    rows.append(
        [green("➕ Add to Group", url=f"https://t.me/{bot_username}?startgroup=true")]
    )
    return InlineKeyboardMarkup(rows)


async def is_admin(update, context):
    chat = update.effective_chat
    user = update.effective_user
    if chat.type == ChatType.PRIVATE or user.id == OWNER_ID:
        return True
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
    except TelegramError:
        return False
    return member.status in ("administrator", "creator")


# ---------------------------------------------------------------- database
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    first_name TEXT,
    username TEXT,
    score INTEGER NOT NULL DEFAULT 0,
    started BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE TABLE IF NOT EXISTS chats (
    chat_id BIGINT PRIMARY KEY,
    title TEXT,
    active BOOLEAN NOT NULL DEFAULT TRUE
);
"""


async def upsert_user(db, user, started=False):
    await db.execute(
        """
        INSERT INTO users (user_id, first_name, username, started)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (user_id) DO UPDATE
        SET first_name = EXCLUDED.first_name,
            username = EXCLUDED.username,
            started = users.started OR EXCLUDED.started
        """,
        user.id,
        user.first_name,
        user.username,
        started,
    )


async def add_point(db, user):
    await db.execute(
        """
        INSERT INTO users (user_id, first_name, username, score)
        VALUES ($1, $2, $3, 1)
        ON CONFLICT (user_id) DO UPDATE
        SET score = users.score + 1,
            first_name = EXCLUDED.first_name,
            username = EXCLUDED.username
        """,
        user.id,
        user.first_name,
        user.username,
    )


async def upsert_chat(db, chat, active=True):
    await db.execute(
        """
        INSERT INTO chats (chat_id, title, active)
        VALUES ($1, $2, $3)
        ON CONFLICT (chat_id) DO UPDATE
        SET title = EXCLUDED.title, active = EXCLUDED.active
        """,
        chat.id,
        chat.title,
        active,
    )


# ---------------------------------------------------------------- game loop
async def game_loop(bot, db, chat_id, state):
    idle = 0
    errors = 0
    try:
        while state["running"]:
            text, options, correct = pick_question(chat_id)
            state["round"] += 1
            rid = state["round"]
            state.update(
                correct=correct,
                winner=None,
                tried=set(),
                event=asyncio.Event(),
                open=True,
            )
            header = f"🧠 <b>မေးခွန်း #{rid}</b>\n\n{html.escape(text)}"
            keyboard = InlineKeyboardMarkup(
                [
                    [green(opt, callback_data=f"ans:{rid}:{i}")]
                    for i, opt in enumerate(options)
                ]
            )
            try:
                msg = await bot.send_message(
                    chat_id,
                    f"{header}\n\n⏱ {ANSWER_SECONDS} စက္ကန့်အတွင်း ဖြေပါ!",
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML,
                )
                errors = 0
            except RetryAfter as e:
                state["open"] = False
                await asyncio.sleep(e.retry_after + 1)
                continue
            except TelegramError as e:
                state["open"] = False
                errors += 1
                log.warning("send failed in %s: %s", chat_id, e)
                if errors >= 3:
                    break
                await asyncio.sleep(5)
                continue

            try:
                await asyncio.wait_for(state["event"].wait(), ANSWER_SECONDS)
            except asyncio.TimeoutError:
                pass
            state["open"] = False

            winner = state["winner"]
            answer_line = f"💡 အဖြေမှန်: <b>{html.escape(options[correct])}</b>"
            if not state["running"]:
                result = "⏹ ဂိမ်း ရပ်လိုက်ပါပြီ။"
            elif winner:
                idle = 0
                result = f"✅ {mention(winner)} အနိုင်ရပါတယ်! (+1 အမှတ်)"
            else:
                idle += 1
                result = "⏰ အချိန်ကုန်သွားပါပြီ၊ ဘယ်သူမှ မမှန်ခဲ့ပါဘူး။"
            try:
                await msg.edit_text(
                    f"{header}\n\n{result}\n{answer_line}", parse_mode=ParseMode.HTML
                )
            except TelegramError:
                pass

            if not state["running"]:
                break
            if idle >= MAX_IDLE_ROUNDS:
                try:
                    await bot.send_message(
                        chat_id,
                        "😴 ဘယ်သူမှ မဖြေတော့လို့ ဂိမ်းကို ခဏရပ်လိုက်ပါတယ်။\n"
                        "ပြန်ဆော့ချင်ရင် /quiz ကို နှိပ်ပါ။",
                    )
                except TelegramError:
                    pass
                break
            await asyncio.sleep(PAUSE_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("game loop crashed in %s", chat_id)
    finally:
        if GAMES.get(chat_id) is state:
            GAMES.pop(chat_id, None)


async def on_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        _, rid, idx = q.data.split(":")
        rid, idx = int(rid), int(idx)
    except ValueError:
        await q.answer()
        return

    state = GAMES.get(q.message.chat.id)
    if not state or state["round"] != rid or not state["open"]:
        await q.answer("⏰ ဒီမေးခွန်း ပြီးသွားပါပြီ")
        return

    user = q.from_user
    if user.id in state["tried"]:
        await q.answer("သင် ဒီမေးခွန်းကို ဖြေပြီးပါပြီ")
        return
    state["tried"].add(user.id)

    if idx == state["correct"]:
        state["winner"] = user
        state["open"] = False
        state["event"].set()
        await q.answer("✅ မှန်ပါတယ်! အမှတ်ရပါပြီ")
        await add_point(context.application.bot_data["db"], user)
    else:
        await q.answer("❌ မှားနေပါတယ်")


# ---------------------------------------------------------------- commands
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = context.application.bot_data["db"]
    chat = update.effective_chat
    if chat.type == ChatType.PRIVATE:
        await upsert_user(db, update.effective_user, started=True)
        await update.message.reply_text(
            INTRO_TEXT,
            parse_mode=ParseMode.HTML,
            reply_markup=intro_keyboard(context.bot.username),
        )
    else:
        await upsert_chat(db, chat)
        await update.message.reply_text(
            "👋 ဉာဏ်စမ်းဂိမ်း စဖို့ /quiz ကို နှိပ်ပါ။ ရပ်ချင်ရင် /stop။"
        )


async def quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = context.application.bot_data["db"]
    chat = update.effective_chat
    if chat.type != ChatType.PRIVATE:
        await upsert_chat(db, chat)
    await upsert_user(db, update.effective_user)

    if chat.id in GAMES:
        await update.message.reply_text("🎮 ဂိမ်း စနေပြီးသားပါ။")
        return
    if not QUESTIONS:
        await update.message.reply_text("မေးခွန်း မရှိသေးပါဘူး။")
        return

    state = {
        "running": True,
        "round": 0,
        "open": False,
        "event": None,
        "winner": None,
        "tried": set(),
        "correct": 0,
    }
    GAMES[chat.id] = state
    await update.message.reply_text(
        f"🎮 ဉာဏ်စမ်းဂိမ်း စပါပြီ! မေးခွန်းတစ်ခုကို {ANSWER_SECONDS} စက္ကန့်စီ ရပါမယ်။"
    )
    state["task"] = asyncio.create_task(game_loop(context.bot, db, chat.id, state))


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    state = GAMES.get(chat.id)
    if not state:
        await update.message.reply_text("ဂိမ်း မစနေပါဘူး။")
        return
    if not await is_admin(update, context):
        await update.message.reply_text("⛔ Group Admin သာ ရပ်လို့ရပါတယ်။")
        return
    state["running"] = False
    if state.get("event"):
        state["event"].set()
    await update.message.reply_text("⏹ ဂိမ်းကို ရပ်လိုက်ပါပြီ။ ပြန်စချင်ရင် /quiz")


async def score_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = context.application.bot_data["db"]
    uid = update.effective_user.id
    row = await db.fetchrow("SELECT score FROM users WHERE user_id = $1", uid)
    if not row or row["score"] == 0:
        await update.message.reply_text("📭 အမှတ် မရသေးပါဘူး။ /quiz နဲ့ စဆော့ကြည့်ပါ!")
        return
    rank = await db.fetchval(
        "SELECT COUNT(*) + 1 FROM users WHERE score > $1", row["score"]
    )
    await update.message.reply_text(
        f"🏅 သင့်အမှတ်: {row['score']}\n🌍 Global အဆင့်: #{rank}"
    )


async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = context.application.bot_data["db"]
    rows = await db.fetch(
        "SELECT user_id, first_name, score FROM users "
        "WHERE score > 0 ORDER BY score DESC LIMIT 10"
    )
    if not rows:
        await update.message.reply_text("Leaderboard မှာ ဘယ်သူမှ မရှိသေးပါဘူး။")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🌍 <b>Global Leaderboard</b>\n"]
    for i, r in enumerate(rows):
        mark = medals[i] if i < 3 else f"{i + 1}."
        name = html.escape(r["first_name"] or "Player")
        lines.append(f"{mark} {name} - {r['score']}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    db = context.application.bot_data["db"]
    total = await db.fetchval("SELECT COUNT(*) FROM users")
    started = await db.fetchval("SELECT COUNT(*) FROM users WHERE started")
    groups = await db.fetchval("SELECT COUNT(*) FROM chats WHERE active")
    await update.message.reply_text(
        f"📊 Users: {total}\n▶️ Start နှိပ်ထားသူ: {started}\n👥 Groups: {groups}\n"
        f"🎮 လက်ရှိ ဆော့နေတဲ့ chat: {len(GAMES)}\n❓ မေးခွန်း: {len(QUESTIONS)}"
    )


# ---------------------------------------------------------------- broadcast
async def do_broadcast(bot, db, owner_chat_id, text, src_chat_id, src_msg_id):
    user_rows = await db.fetch("SELECT user_id FROM users WHERE started")
    chat_rows = await db.fetch("SELECT chat_id FROM chats WHERE active")
    targets = [("user", r["user_id"]) for r in user_rows] + [
        ("chat", r["chat_id"]) for r in chat_rows
    ]
    ok = failed = 0
    for kind, target in targets:
        for attempt in range(2):
            try:
                if src_msg_id:
                    await bot.copy_message(target, src_chat_id, src_msg_id)
                else:
                    await bot.send_message(target, text)
                ok += 1
                break
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except (Forbidden, BadRequest):
                failed += 1
                if kind == "user":
                    await db.execute(
                        "UPDATE users SET started = FALSE WHERE user_id = $1", target
                    )
                else:
                    await db.execute(
                        "UPDATE chats SET active = FALSE WHERE chat_id = $1", target
                    )
                break
            except TelegramError:
                failed += 1
                break
        await asyncio.sleep(0.05)
    await bot.send_message(
        owner_chat_id,
        f"📢 Broadcast ပြီးပါပြီ\n✅ အောင်မြင်: {ok}\n❌ မအောင်မြင်: {failed}",
    )


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    msg = update.message
    reply = msg.reply_to_message
    parts = msg.text.split(None, 1)
    text = parts[1] if len(parts) > 1 else ""
    if not reply and not text:
        await msg.reply_text(
            "အသုံးပြုပုံ:\n/broadcast စာသား\nဒါမှမဟုတ် ပို့ချင်တဲ့ message ကို reply လုပ်ပြီး /broadcast"
        )
        return
    db = context.application.bot_data["db"]
    await msg.reply_text("📢 ပို့နေပါပြီ... ပြီးရင် အစီရင်ခံပါမယ်။")
    context.application.create_task(
        do_broadcast(
            context.bot,
            db,
            msg.chat.id,
            text,
            msg.chat.id if reply else None,
            reply.message_id if reply else None,
        )
    )


# ---------------------------------------------------------------- membership
async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = context.application.bot_data["db"]
    change = update.my_chat_member
    chat = change.chat
    new_status = change.new_chat_member.status
    if chat.type == ChatType.PRIVATE:
        if new_status == "kicked":  # user က bot ကို block လုပ်ထား
            await db.execute(
                "UPDATE users SET started = FALSE WHERE user_id = $1", chat.id
            )
        return
    active = new_status in ("member", "administrator")
    await upsert_chat(db, chat, active)
    if not active:
        state = GAMES.get(chat.id)
        if state:
            state["running"] = False
            if state.get("event"):
                state["event"].set()


# ---------------------------------------------------------------- main
async def post_init(app):
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)
    app.bot_data["db"] = pool
    await app.bot.set_my_commands(
        [
            BotCommand("start", "ဘော့အကြောင်း / စတင်ရန်"),
            BotCommand("quiz", "ဉာဏ်စမ်းဂိမ်း စမယ်"),
            BotCommand("stop", "ဂိမ်း ရပ်မယ်"),
            BotCommand("score", "ကိုယ့်အမှတ်"),
            BotCommand("top", "Global leaderboard"),
        ]
    )
    log.info("Bot started as @%s", app.bot.username)


async def post_shutdown(app):
    pool = app.bot_data.get("db")
    if pool:
        await pool.close()


def main():
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("quiz", quiz_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("score", score_cmd))
    app.add_handler(CommandHandler("top", top_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CallbackQueryHandler(on_answer, pattern=r"^ans:"))
    app.add_handler(
        ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER)
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
