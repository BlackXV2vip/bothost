# ============================================================
# بوت هوست 🤖 — بوت تليجرام بيستضيف بوتات بايثون تانية
# ترفع له ملف .py وهو بيثبّت المكتبات ويشغّله ويدّيك التحكم
# شغال على Render كـ Web Service + self-ping عشان مينامش
# ============================================================
import asyncio
import html
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    MessageHandler, filters,
)

from manager import BotManager

# ---------- الإعدادات ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT = int(os.environ.get("PORT", 3002))
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
OPEN_UPLOADS = os.environ.get("OPEN_UPLOADS", "false").lower() == "true"
MAX_BOTS = int(os.environ.get("MAX_BOTS", "3"))
MAX_FILE_MB = 1

ADMIN_FILE = Path("storage/admin.json")
manager = BotManager(max_bots=MAX_BOTS)

# ---------- الأدمن ----------
def load_admins():
    global ADMIN_IDS
    if ADMIN_FILE.exists():
        try:
            ADMIN_IDS |= set(json.loads(ADMIN_FILE.read_text()))
        except Exception:
            pass

def save_admin(uid):
    data = []
    if ADMIN_FILE.exists():
        try:
            data = json.loads(ADMIN_FILE.read_text())
        except Exception:
            pass
    if uid not in data:
        data.append(uid)
        ADMIN_FILE.parent.mkdir(parents=True, exist_ok=True)
        ADMIN_FILE.write_text(json.dumps(data))

def is_admin(uid):
    return uid in ADMIN_IDS

load_admins()


def first_admin_hook(update: Update) -> bool:
    """لو مفيش أدمن لسه، أول واحد يكلّم البوت بيبقى الأدمن."""
    if not ADMIN_IDS and update.effective_user:
        save_admin(update.effective_user.id)
        ADMIN_IDS.add(update.effective_user.id)
        return True
    return False


def esc(t):
    return html.escape(str(t))


# ---------- سيرفر الصحة (عشان Render) ----------
class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true, "service": "bothost"}')

    def log_message(self, *a):
        pass

def start_health_server():
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", PORT), Health)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        print(f"✅ Health server على البورت {PORT}")
    except Exception as e:
        print(f"⚠️ Health server: {e}")


# ---------- self-ping (عشان الخطة المجانية مينامش) ----------
async def self_ping(context: ContextTypes.DEFAULT_TYPE):
    if not RENDER_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            await c.get(f"{RENDER_URL}/")
    except Exception:
        pass


# ---------- الأوامر ----------
async def cmd_start(update: Update, ctx):
    was_set = first_admin_hook(update)
    admin_note = "\n👑 إنت الأدمن الأول — البوت بتاعك دلوقتي!" if was_set else ""
    await update.message.reply_text(
        "🤖 <b>أهلاً في بوت هوست!</b>\n"
        "البوت ده بيستضيف بوتات بايثون تانية ليك — ترفع الملف وهو بيوصّلها شغالة.\n\n"
        "📌 <b>الطريقة:</b>\n"
        "1️⃣ ابعت ملف <code>.py</code> (كود البوت بتاعك)\n"
        "2️⃣ لو بوتك محتاج مكتبات، ابعت <code>requirements.txt</code>\n"
        "3️⃣ البوت هيثبّت المكتبات ويشغّل بوتك ويديك أزرار التحكم\n\n"
        "🛠 <b>الأوامر:</b>\n"
        "/mybots — البوتات بتاعتك وحالتها\n"
        "/id — رقمك على تليجرام\n"
        "/help — المساعدة" + admin_note,
        parse_mode=ParseMode.HTML,
    )


async def cmd_help(update: Update, ctx):
    await update.message.reply_text(
        "ℹ️ <b>إزاي تستضيف بوتك؟</b>\n\n"
        "1️⃣ ارفع ملف <code>.py</code> — يكون فيه الكود اللي بيشتغل على طول "
        "(مثلاً بوت تليجرام بينفذ <code>run_polling()</code> أو لوب لا نهائي)\n"
        "2️⃣ لو محتاج مكتبات (زي python-telegram-bot) ابععت ملف "
        "<code>requirements.txt</code> فيه أسماءها\n"
        "3️⃣ استنى التثبيت والتشغيل — وشوف اللوج للتأكد\n\n"
        "⚠️ <b>حدود:</b>\n"
        f"• أقصى حجم للملف {MAX_FILE_MB} ميجا\n"
        f"• كل بوت ليه حد ذاكرة 150 ميجا\n"
        f"• أقصى عدد بوتات {MAX_BOTS}\n"
        "• التثبيت بياخد أقصى 5 دقايق",
        parse_mode=ParseMode.HTML,
    )


async def cmd_id(update: Update, ctx):
    u = update.effective_user
    await update.message.reply_text(f"🆔 رقمك: <code>{u.id}</code>", parse_mode=ParseMode.HTML)


def bot_card(b, with_buttons=True):
    status = "🟢 شغال" if b.running else "🔴 واقف"
    txt = (
        f"🤖 <b>{esc(b.meta['name'])}</b>\n"
        f"🆔 <code>{b.id}</code>\n"
        f"الحالة: {status}\n"
        f"📦 مكتبات: {'✅' if b.req_file.exists() else '❌'}"
    )
    kb = None
    if with_buttons:
        rows = []
        if b.running:
            rows.append([InlineKeyboardButton("⏹ إيقاف", callback_data=f"stop_{b.id}")])
        else:
            rows.append([InlineKeyboardButton("▶️ تشغيل", callback_data=f"run_{b.id}")])
        rows.append([
            InlineKeyboardButton("📜 اللوج", callback_data=f"logs_{b.id}"),
            InlineKeyboardButton("🗑 حذف", callback_data=f"del_{b.id}"),
        ])
        kb = InlineKeyboardMarkup(rows)
    return txt, kb


async def cmd_mybots(update: Update, ctx):
    uid = update.effective_user.id
    bots = manager.list_bots(uid if not is_admin(uid) else None)
    if not bots:
        await update.message.reply_text("📭 مفيش بوتات لسه — ابععت ملف .py الأول!")
        return
    for b in bots:
        txt, kb = bot_card(b)
        await update.message.reply_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)


async def on_document(update: Update, ctx):
    u = update.effective_user
    first_admin_hook(update)
    doc = update.message.document
    name = doc.file_name or "file.txt"

    if not (is_admin(u.id) or OPEN_UPLOADS):
        await update.message.reply_text("⛔️ الرفع للأدمن بس دلوقتي — كلّم صاحب البوت")
        return

    if doc.file_size > MAX_FILE_MB * 1024 * 1024:
        await update.message.reply_text(f"⚠️ الملف أكبر من {MAX_FILE_MB} ميجا")
        return

    tg_file = await doc.get_file()

    # ملف requirements.txt
    if name.lower() == "requirements.txt":
        target = next((b for b in manager.list_bots(u.id if not is_admin(u.id) else None)
                       if not b.req_file.exists()), None)
        if target is None:
            await update.message.reply_text(
                " ℹ️ ابععت ملف البوت .py الأول، وبعدين requirements.txt")
            return
        data = await tg_file.download_as_bytearray()
        target.req_file.write_bytes(data)
        await update.message.reply_text(
            f"📦 استلمت requirements بتاعة <b>{esc(target.meta['name'])}</b>\n"
            "⏳ بثبّت المكتبات... (ممكن ياخد دقيقة)", parse_mode=ParseMode.HTML)
        ok, out = await asyncio.to_thread(target.install)
        if not ok:
            await update.message.reply_text(
                f"❌ فشل التثبيت:\n<code>{esc(out[-800:])}</code>\n\nصلّح الملف وابعته تاني",
                parse_mode=ParseMode.HTML)
            return
        was_running = target.meta.get("auto_run")
        if was_running:
            target.stop()
        ok2, _ = target.start()
        await update.message.reply_text(
            "✅ المكتبات اتثبتت وخلاص!" + ("\n▶️ والبوت رجع اشتغل" if ok2 else ""),
            parse_mode=ParseMode.HTML)
        return

    # ملف بايثون — بوت جديد
    if not name.lower().endswith(".py"):
        await update.message.reply_text("🤔 ابعت ملف .py أو requirements.txt")
        return

    data = await tg_file.download_as_bytearray()
    bot, err = manager.create(u.id, u.first_name or "مستخدم", name, bytes(data))
    if err:
        await update.message.reply_text(f"⚠️ {err}")
        return

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("▶️ شغّله من غير مكتبات", callback_data=f"run_{bot.id}"),
        InlineKeyboardButton("📦 هبعت requirements", callback_data=f"waitreq_{bot.id}"),
    ]])
    await update.message.reply_text(
        f"✅ استلمت <b>{esc(name)}</b>\n🆔 الكود: <code>{bot.id}</code>\n\n"
        "البوت بتاعك محتاج مكتبات خارجية؟",
        parse_mode=ParseMode.HTML, reply_markup=kb)


# ---------- الأزرار ----------
async def on_button(update: Update, ctx):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    action, _, bot_id = q.data.partition("_")
    b = manager.get(bot_id)
    if not b:
        await q.edit_message_text("⚠️ البوت ده اتمسح خلاص")
        return
    if not (is_admin(uid) or b.meta.get("owner_id") == uid or OPEN_UPLOADS):
        await q.answer("⛔️ ده مش بوتك!", show_alert=True)
        return

    if action == "run":
        await q.edit_message_text(f"⏳ بشغّل <b>{esc(b.meta['name'])}</b>...", parse_mode=ParseMode.HTML)
        if b.req_file.exists() and not b.libs_dir.exists():
            ok, out = await asyncio.to_thread(b.install)
            if not ok:
                await q.edit_message_text(
                    f"❌ فشل تثبيت المكتبات:\n<code>{esc(out[-800:])}</code>", parse_mode=ParseMode.HTML)
                return
        ok, msg = b.start()
        import time as _t
        await asyncio.sleep(2)  # نبص البوت يشتغل أو يقع
        alive = b.running
        txt, kb = bot_card(b)
        note = ""
        if ok and not alive:
            note = "\n\n⚠️ <b>البوت وقع بعد التشغيل!</b> افتح اللوج وشوف السبب"
        await q.edit_message_text(txt + note, parse_mode=ParseMode.HTML, reply_markup=kb)

    elif action == "waitreq":
        await q.edit_message_text(
            f"📦 تمام — ابعت دلوقتي ملف <code>requirements.txt</code>\n"
            f"(الكود: <code>{b.id}</code>)",
            parse_mode=ParseMode.HTML)

    elif action == "stop":
        b.stop()
        txt, kb = bot_card(b)
        await q.edit_message_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)

    elif action == "logs":
        log = b.logs()
        await q.message.reply_text(
            f"📜 <b>لوج {esc(b.meta['name'])}</b> (آخر 40 سطر):\n"
            f"<code>{esc(log[-2500:])}</code>",
            parse_mode=ParseMode.HTML)

    elif action == "del":
        b.delete()
        await q.edit_message_text("🗑 البوت اتمسح خلاص")


# ---------- التشغيل ----------
def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ ناقص BOT_TOKEN في متغيرات البيئة")

    start_health_server()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler(["start", "help2"], cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("mybots", cmd_mybots))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))

    # إعادة تشغيل البوتات المحفوظة
    started = manager.restart_all()
    print(f"🔁 رجّعت شغل {len(started)} بوت: {started}")

    # self-ping كل 10 دقايق
    if RENDER_URL and app.job_queue:
        app.job_queue.run_repeating(self_ping, interval=600, first=120)
        print(f"🔄 self-ping مفعّل على {RENDER_URL}")

    print("🤖 بوت هوست شغال!")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
