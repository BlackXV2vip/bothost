# ============================================================
# بوت هوست v3 🤖 — منصة استضافة بوتات بايثون كاملة
# لوحة تحكم للأدمن • مشاريع zip متعددة الملفات • سعة أكبر
# تثبيت مكتبات تلقائي • تخزين دائم • إيقاف/تشغيل/لوجات
# ============================================================
import asyncio
import html
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, BotCommand, ReplyKeyboardRemove
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    MessageHandler, filters,
)

from deps import detect_packages
from manager import BotManager, unpack_zip, system_memory_mb, fmt_size
import persist

# ============================================================
# الإعدادات
# ============================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT = int(os.environ.get("PORT", 3002))
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
OPEN_UPLOADS = os.environ.get("OPEN_UPLOADS", "false").lower() == "true"
MAX_BOTS = int(os.environ.get("MAX_BOTS", "8"))
MAX_RUNNING = int(os.environ.get("MAX_RUNNING", "4"))
PER_USER = int(os.environ.get("PER_USER", "3"))
MAX_FILE_MB = 1
MAX_ZIP_MB = 8

MAIN_START = time.time()

ADMIN_FILE = Path("storage/admin.json")
manager = BotManager(max_bots=MAX_BOTS, max_running=MAX_RUNNING, per_user=PER_USER)

INSTALLING = set()      # بوتات في مرحلة تثبيت
PENDING_REQS = {}       # مستخدم → بوت مستني منه requirements
PENDING_BCAST = set()   # أدمنز مستنيين نص الإذاعة
BCAST_DRAFT = {}        # أدمن → نص الإذاعة


# ============================================================
# الأدمن
# ============================================================
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
    if not ADMIN_IDS and update.effective_user:
        save_admin(update.effective_user.id)
        ADMIN_IDS.add(update.effective_user.id)
        return True
    return False


def esc(t):
    return html.escape(str(t))


def fmt_uptime():
    s = int(time.time() - MAIN_START)
    h, m = s // 3600, (s % 3600) // 60
    return f"{h} ساعة {m} دقيقة" if h else f"{m} دقيقة"


# ============================================================
# القائمة الرئيسية — أزرار inline تحت الرسايل
# ============================================================
MENU_TEXT = "🤖 <b>بوت هوست — القائمة الرئيسية</b>\nاختار من الأزرار تحت 👇"


def menu_kb(admin=False):
    rows = [[InlineKeyboardButton("بوتاتي", callback_data="ubots", style="primary")]]
    if admin:
        rows.append([InlineKeyboardButton("لوحة التحكم", callback_data="panel", style="primary")])
    rows.append([InlineKeyboardButton("المساعدة", callback_data="uhelp")])
    return InlineKeyboardMarkup(rows)


def user_bots_kb(uid):
    bots = manager.list_bots(uid if not is_admin(uid) else None)
    rows = []
    for b in bots:
        rows.append([InlineKeyboardButton(
            b.meta.get("name", b.id)[:24],
            callback_data=f"ubot_{b.id}",
            style="success" if b.running else "danger")])
    rows.append([InlineKeyboardButton("القائمة الرئيسية", callback_data="umenu")])
    return bots, InlineKeyboardMarkup(rows)



# ============================================================
# سيرفر الصحة
# ============================================================
class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true, "service": "bothost", "version": 3}')

    def log_message(self, *a):
        pass


def start_health_server():
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", PORT), Health)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        print(f"✅ Health server على البورت {PORT}")
    except Exception as e:
        print(f"⚠️ Health server: {e}")


async def self_ping(context: ContextTypes.DEFAULT_TYPE):
    if not RENDER_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            await c.get(f"{RENDER_URL}/")
    except Exception:
        pass


# ============================================================
# أوامر عامة
# ============================================================
async def cmd_start(update: Update, ctx):
    was_set = first_admin_hook(update)
    admin = is_admin(update.effective_user.id)
    admin_note = "\n👑 إنت الأدمن في المنصة" if was_set or admin else ""
    # نشيل أي كيبورد قديم من النسخ اللي فاتت
    await update.message.reply_text("✨", reply_markup=ReplyKeyboardRemove())
    await update.message.reply_text(
        "🤖 <b>أهلاً في بوت هوست v3!</b>\n"
        "منصة استضافة بوتات بايثون كاملة:\n\n"
        "📄 ابعت ملف <code>.py</code> — بوت بمكتباته\n"
        "📦 أو <code>zip</code> — مشروع كامل بملفات متعددة\n\n"
        "🔍 بأكتشف المكتبات لوحدي وأثبتهم بزرار واحدة\n"
        "☁️ بوتاتك محفوظة على السحابة\n"
        "استخدم الأزرار تحت الرسالة للتنقل 👇" + admin_note,
        parse_mode=ParseMode.HTML,
        reply_markup=menu_kb(admin),
    )


async def cmd_help(update: Update, ctx):
    await update.message.reply_text(
        "ℹ️ <b>الدليل الكامل</b>\n\n"
        "1️⃣ ابععت <code>.py</code> أو <code>zip</code> (مشروع بملفات متعددة — "
        "لازم فيه main.py أو bot.py)\n"
        "2️⃣ أنا باكتشف المكتبات من الكود تلقائياً 🔍\n"
        "3️⃣ دوس <b>«📦 ثبّتها وشغّل»</b> وخلاص!\n\n"
        "🎛 <b>التحكم في كل بوت:</b>\n"
        "▶️ تشغيل • ⏹ إيقاف • 🔄 إعادة تشغيل\n"
        "📜 اللوج الحي • ♻️ إعادة تثبيت المكتبات • 🗑 حذف\n\n"
        "⚡️ <b>الحدود:</b>\n"
        f"• إجمالي البوتات: {MAX_BOTS} (و{PER_USER} لكل مستخدم)\n"
        f"• شغالة في نفس الوقت: {MAX_RUNNING} (حد ذاكرة السيرفر)\n"
        "• ذاكرة كل بوت: 150 MB • ملف: "
        f"{MAX_FILE_MB}MB • مشروع مضغوط: {MAX_ZIP_MB}MB\n"
        "• البوتات بترجع تشتغل لوحدها بعد أي صيانة ☁️",
        parse_mode=ParseMode.HTML,
    )


async def cmd_id(update: Update, ctx):
    u = update.effective_user
    admin = "\n👑 أدمن" if is_admin(u.id) else ""
    await update.message.reply_text(
        f"🆔 رقمك: <code>{u.id}</code>{admin}", parse_mode=ParseMode.HTML)


# ============================================================
# كروت البوتات
# ============================================================
def bot_card(b, admin_view=False):
    if b.running:
        mem = b.mem_mb()
        status = f"🟢 شغال" + (f" (ذاكرة {mem} MB)" if mem else "")
    else:
        status = "🔴 واقف"
    m = b.meta
    txt = (
        f"🤖 <b>{esc(m.get('name', '?'))}</b>\n"
        f"🆔 <code>{b.id}</code>\n"
        f"👤 {esc(m.get('owner_name', '?'))} | 📄 {esc(m.get('file', 'main.py'))}\n"
        f"الحالة: {status}\n"
        f"📦 مكتبات: {'✅' if b.req_file.exists() else '❌'} | "
        f"📁 الحجم: {fmt_size(b.size_bytes())}\n"
        f"▶️ تشغيلات: {m.get('starts', 0)} | 🕐 {esc(m.get('last_start', m.get('created', '—')))}"
    )
    rows = []
    if b.running:
        rows.append([
            InlineKeyboardButton("إيقاف", callback_data=f"stop_{b.id}", style="danger"),
            InlineKeyboardButton("إعادة تشغيل", callback_data=f"rst_{b.id}", style="primary"),
        ])
    else:
        rows.append([
            InlineKeyboardButton("تشغيل", callback_data=f"run_{b.id}", style="success"),
            InlineKeyboardButton("إعادة تشغيل", callback_data=f"rst_{b.id}", style="primary"),
        ])
    second = [InlineKeyboardButton("اللوج", callback_data=f"logs_{b.id}", style="primary")]
    if b.req_file.exists():
        second.append(InlineKeyboardButton("المكتبات", callback_data=f"inst_{b.id}", style="primary"))
    second.append(InlineKeyboardButton("حذف", callback_data=f"del_{b.id}", style="danger"))
    rows.append(second)
    if admin_view:
        rows.append([InlineKeyboardButton("للوحة التحكم", callback_data="pbots_0")])
    else:
        rows.append([InlineKeyboardButton("لبوتاتي", callback_data="ubots")])
    return txt, InlineKeyboardMarkup(rows)


async def cmd_mybots(update: Update, ctx):
    uid = update.effective_user.id
    bots = manager.list_bots(uid if not is_admin(uid) else None)
    if not bots:
        await update.message.reply_text(
            "📭 مفيش بوتات لسه!\nابععت ملف .py أو zip ويلا نبدأ 🚀")
        return
    running = sum(1 for b in bots if b.running)
    await update.message.reply_text(
        f"🤖 <b>بوتاتك ({len(bots)}) — شغال منها {running} 🟢</b>",
        parse_mode=ParseMode.HTML)
    for b in bots:
        txt, kb = bot_card(b)
        await update.message.reply_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)


# ============================================================
# لوحة تحكم الأدمن 🖥
# ============================================================
def panel_text():
    bots = manager.list_bots()
    running = sum(1 for b in bots if b.running)
    total_mem, avail_mem = system_memory_mb()
    used = (total_mem - avail_mem) if (total_mem and avail_mem) else None
    mem_line = f"{used}/{total_mem} MB (متاح {avail_mem})" if total_mem else "؟"
    size = sum(b.size_bytes() for b in bots)
    starts = sum(b.meta.get("starts", 0) for b in bots)
    return (
        "🖥 <b>لوحة تحكم بوت هوست</b>\n"
        "═══════════════\n"
        f"🤖 البوتات: <b>{len(bots)}/{MAX_BOTS}</b> (🟢 {running})\n"
        f"👥 المستخدمون: {manager.users_count()}\n"
        f"▶️ إجمالي التشغيلات: {starts}\n"
        f"⏱ مدة تشغيل السيرفر: {fmt_uptime()}\n"
        f"💾 ذاكرة السيرفر: {mem_line}\n"
        f"📁 مساحة البوتات: {fmt_size(size)}\n"
        f"☁️ التخزين الدائم: {'✅ مفعّل' if persist.ENABLED else '❌ مقفول'}"
    )


def panel_kb():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("إحصائيات", callback_data="pstats", style="primary"),
            InlineKeyboardButton("البوتات", callback_data="pbots_0", style="primary"),
        ],
        [
            InlineKeyboardButton("النظام", callback_data="psys", style="primary"),
            InlineKeyboardButton("صيانة", callback_data="pmaint"),
        ],
        [InlineKeyboardButton("إذاعة للمستخدمين", callback_data="pbcast", style="primary")],
        [InlineKeyboardButton("تحديث اللوحة", callback_data="panel")],
    ])


async def cmd_panel(update: Update, ctx):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            f"⛔️ دي للأدمن بس.\nرقمك: <code>{update.effective_user.id}</code>\n"
            "لو ده رقمك الصحيح ابعته لمطور البوت يضيفه.",
            parse_mode=ParseMode.HTML)
        return
    await update.message.reply_text(
        panel_text(), parse_mode=ParseMode.HTML, reply_markup=panel_kb())


def bots_page_kb(page):
    bots = manager.list_bots()
    per, pages = 4, max(1, (len(bots) + 3) // 4)
    page = max(0, min(page, pages - 1))
    chunk = bots[page * per:(page + 1) * per]
    rows = []
    for b in chunk:
        rows.append([InlineKeyboardButton(
            b.meta.get('name', b.id)[:24],
            callback_data=f"pbot_{b.id}",
            style="success" if b.running else "danger")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("السابق", callback_data=f"pbots_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("التالي", callback_data=f"pbots_{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("رجوع للوحة", callback_data="panel")])
    return f"📋 <b>كل البوتات ({len(bots)})</b> — دوس على بوت للتحكم", InlineKeyboardMarkup(rows)


def maint_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("إعادة تشغيل الشغالين", callback_data="mrestart", style="primary")],
        [InlineKeyboardButton("إيقاف الكل", callback_data="mstopall", style="danger")],
        [InlineKeyboardButton("مزامنة الكل للسحابة", callback_data="msync", style="success")],
        [InlineKeyboardButton("رجوع للوحة", callback_data="panel")],
    ])


# ============================================================
# استقبال الملفات
# ============================================================
async def _offer_start(b, pkgs, extra=""):
    """شاشة ما بعد الرفع: أزرار التثبيت/التشغيل."""
    if pkgs:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"ثبّتها وشغّل ({len(pkgs)} مكتبة)", callback_data=f"inst_{b.id}", style="success")],
            [
                InlineKeyboardButton("من غير تثبيت", callback_data=f"run_{b.id}", style="primary"),
                InlineKeyboardButton("هكتبها بنفسي", callback_data=f"waitreq_{b.id}"),
            ],
        ])
        await b._msg.reply_text(
            f"✅ استلمت <b>{esc(b.meta['name'])}</b>{extra}\n"
            f"🔍 <b>لقيت المكتبات دي:</b>\n<code>{esc(', '.join(pkgs))}</code>\n\n"
            "دوس الزرار وأنا أثبتهم وأشغّل 👇",
            parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("شغّله على طول", callback_data=f"run_{b.id}", style="success")]])
        await b._msg.reply_text(
            f"✅ استلمت <b>{esc(b.meta['name'])}</b>{extra}\n"
            "😎 محتاج مكتبات قياسية بس — جاهز للتشغيل الفوري!",
            parse_mode=ParseMode.HTML, reply_markup=kb)


async def on_document(update: Update, ctx):
    u = update.effective_user
    first_admin_hook(update)
    doc = update.message.document
    name = doc.file_name or "file.txt"

    if not (is_admin(u.id) or OPEN_UPLOADS):
        await update.message.reply_text("⛔️ الرفع للأدمن بس دلوقتي — كلّم صاحب البوت")
        return

    # ----- requirements.txt -----
    if name.lower() == "requirements.txt":
        target = None
        if u.id in PENDING_REQS:
            target = manager.get(PENDING_REQS.pop(u.id))
        if target is None:
            target = next((b for b in manager.list_bots(u.id if not is_admin(u.id) else None)
                           if not b.req_file.exists()), None)
        if target is None:
            await update.message.reply_text("ℹ️ ابععت ملف البوت الأول، وبعدين requirements.txt")
            return
        data = await tg_download(doc)
        target.req_file.write_bytes(data)
        persist.push_bot_async(target)
        await update.message.reply_text(
            f"📦 استلمت requirements بتاعة <b>{esc(target.meta['name'])}</b>\n⏳ بثبّت...",
            parse_mode=ParseMode.HTML)
        ok, out = await asyncio.to_thread(target.install)
        if not ok:
            await update.message.reply_text(
                f"❌ فشل التثبيت:\n<code>{esc(out[-800:])}</code>", parse_mode=ParseMode.HTML)
            return
        if target.meta.get("auto_run"):
            target.stop()
        ok2, _ = target.start()
        await update.message.reply_text(
            "✅ اتثبتت!" + ("\n▶️ والبوت رجع اشتغل" if ok2 else ""), parse_mode=ParseMode.HTML)
        return

    # ----- مشروع مضغوط zip -----
    if name.lower().endswith(".zip"):
        if doc.file_size > MAX_ZIP_MB * 1024 * 1024:
            await update.message.reply_text(f"⚠️ الملف أكبر من {MAX_ZIP_MB} ميجا")
            return
        bot, err = manager.allocate(u.id, u.first_name or "مستخدم", name)
        if err:
            await update.message.reply_text(f"⚠️ {err}")
            return
        data = await tg_download(doc)
        try:
            entry, reqs = await asyncio.to_thread(unpack_zip, bytes(data), bot.dir)
        except ValueError as e:
            bot.delete()
            await update.message.reply_text(f"⚠️ {esc(str(e))}")
            return
        except Exception as e:
            bot.delete()
            await update.message.reply_text(f"❌ ملف الضغط فيه مشكلة: {esc(str(e)[:100])}")
            return
        bot.meta["file"] = entry
        bot.save_meta()
        persist.push_bot_async(bot)
        nfiles = len([p for p in bot.dir.rglob("*") if p.is_file()])
        code = bot.main_file.read_text(encoding="utf-8", errors="replace")
        pkgs = detect_packages(code)
        bot._msg = update.message
        await _offer_start(
            bot, pkgs,
            extra=f"\n🎓 <b>مشروع كامل!</b> {nfiles} ملف — ملف التشغيل: <code>{esc(entry)}</code>")
        return

    # ----- ملف بايثون مفرد -----
    if not name.lower().endswith(".py"):
        await update.message.reply_text("🤔 ابعت .py أو .zip أو requirements.txt")
        return
    if doc.file_size > MAX_FILE_MB * 1024 * 1024:
        await update.message.reply_text(f"⚠️ الملف أكبر من {MAX_FILE_MB} ميجا")
        return

    data = await tg_download(doc)
    bot, err = manager.create(u.id, u.first_name or "مستخدم", name, bytes(data))
    if err:
        await update.message.reply_text(f"⚠️ {err}")
        return
    pkgs = detect_packages(bytes(data).decode("utf-8", errors="replace"))
    bot._msg = update.message
    await _offer_start(bot, pkgs)


async def tg_download(doc):
    f = await doc.get_file()
    return bytes(await f.download_as_bytearray())


# ============================================================
# الإذاعة
# ============================================================
async def on_text(update: Update, ctx):
    u = update.effective_user

    # ===== نص الإذاعة =====
    if u.id in PENDING_BCAST:
        PENDING_BCAST.discard(u.id)
        BCAST_DRAFT[u.id] = update.message.text
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("ابعتها", callback_data="bcast_go", style="success"),
            InlineKeyboardButton("إلغاء", callback_data="bcast_no", style="danger"),
        ]])
        await update.message.reply_text(
            f"📢 <b>معاينة الإذاعة:</b>\n\n{esc(update.message.text)}\n\nتبعت؟",
            parse_mode=ParseMode.HTML, reply_markup=kb)


async def do_broadcast(q, ctx):
    uid = q.from_user.id
    text = BCAST_DRAFT.pop(uid, None)
    if text is None:
        await q.edit_message_text("⚠️ مفيش نص محفوظ — ابدأ من اللوحة تاني")
        return
    users = {b.meta.get("owner_id") for b in manager.list_bots() if b.meta.get("owner_id")}
    users |= ADMIN_IDS
    await q.edit_message_text(f"📡 ببعت لـ {len(users)} مستخدم...")
    ok = fail = 0
    for pid in users:
        try:
            await ctx.bot.send_message(chat_id=pid, text=f"📢 <b>رسالة من الإدارة:</b>\n\n{esc(text)}",
                                       parse_mode=ParseMode.HTML)
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.15)
    await q.edit_message_text(f"📢 الإذاعة خلصت:\n✅ وصلت لـ {ok}\n❌ فشل: {fail}")


# ============================================================
# الأزرار
# ============================================================
def check_access(q, b):
    uid = q.from_user.id
    if not (is_admin(uid) or b.meta.get("owner_id") == uid or OPEN_UPLOADS):
        return False
    return True


async def start_and_report(q, b):
    ok, _msg = b.start()
    await asyncio.sleep(2)
    alive = b.running
    txt, kb = bot_card(b, admin_view=is_admin(q.from_user.id))
    note = ""
    if ok and not alive:
        log_tail = b.logs(6)
        note = f"\n\n⚠️ <b>البوت وقع بعد التشغيل!</b>\nآخر اللوج:\n<code>{esc(log_tail[-400:])}</code>"
    elif ok and alive:
        note = "\n\n🟢 <b>شغال دلوقتي!</b>"
    await q.edit_message_text(txt + note, parse_mode=ParseMode.HTML, reply_markup=kb)


async def on_button(update: Update, ctx):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data

    # ---------- قائمة المستخدم الرئيسية ----------
    if data == "umenu":
        await q.edit_message_text(
            MENU_TEXT, parse_mode=ParseMode.HTML, reply_markup=menu_kb(is_admin(uid)))
        return

    if data == "uhelp":
        await q.edit_message_text(
            "ℹ️ <b>الدليل السريع</b>\n\n"
            "1️⃣ ابعت <code>.py</code> أو <code>zip</code>\n"
            "2️⃣ أدوس «ثبّتها وشغّل»\n"
            "3️⃣ تحكم بأزرار كل بوت: تشغيل/إيقاف/لوج/حذف\n\n"
            "⚡ الحدود: 8 بوتات (3 لكل مستخدم) • 4 شغالة مع بعض\n"
            "☁️ بوتاتك بترجع لوحدها بعد أي صيانة",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("القائمة الرئيسية", callback_data="umenu")]]))
        return

    if data == "ubots":
        bots, kb = user_bots_kb(uid)
        if not bots:
            await q.edit_message_text(
                "📭 مفيش بوتات لسه!\nابععت ملف .py أو zip وهنبدأ 🚀",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("القائمة الرئيسية", callback_data="umenu")]]))
        else:
            running = sum(1 for b in bots if b.running)
            await q.edit_message_text(
                f"🤖 <b>بوتاتك ({len(bots)}) — شغال {running} 🟢</b>\nاختار بوت للتحكم:",
                parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data.startswith("ubot_"):
        b = manager.get(data.split("_", 1)[1])
        if not b:
            await q.edit_message_text("⚠️ البوت ده اتمسح")
            return
        if not (is_admin(uid) or b.meta.get("owner_id") == uid):
            await q.answer("⛔️ ده مش بوتك!", show_alert=True)
            return
        txt, kb = bot_card(b, admin_view=False)
        await q.edit_message_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    # ---------- لوحة الأدمن ----------
    if data == "noop":
        return
    if data in ("panel", "pstats", "psys", "pmaint", "pbcast", "bcast_go", "bcast_no",
                "mrestart", "mstopall", "msync") or data.startswith("pbots_") or data.startswith("pbot_"):
        if not is_admin(uid):
            await q.answer("⛔️ للأدمن بس!", show_alert=True)
            return

    if data == "panel":
        await q.edit_message_text(panel_text(), parse_mode=ParseMode.HTML, reply_markup=panel_kb())
        return

    if data == "pstats":
        await q.edit_message_text(panel_text() + "\n\n📊 <b>التفاصيل فوق</b>",
                                  parse_mode=ParseMode.HTML, reply_markup=panel_kb())
        return

    if data == "psys":
        total, avail = system_memory_mb()
        bots = manager.list_bots()
        lines = ["🖥 <b>معلومات النظام</b>\n═══════════════"]
        import platform
        lines.append(f"🐍 Python {platform.python_version()}")
        lines.append(f"🧠 المعالجات: {os.cpu_count()}")
        lines.append(f"💾 الذاكرة: {(total-avail) if total and avail else '?'} / {total} MB")
        for b in bots:
            if b.running:
                lines.append(f"  ├ 🟢 {esc(b.meta['name'][:20])}: {b.mem_mb()} MB")
        lines.append(f"☁️ التخزين: {'GitHub ✅' if persist.ENABLED else '❌'}")
        lines.append(f"🌐 الرابط: {esc(RENDER_URL or 'محلي')}")
        await q.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=maint_kb())
        return

    if data == "pmaint":
        await q.edit_message_text(
            "🧰 <b>الصيانة</b> — اختار عملية:",
            parse_mode=ParseMode.HTML, reply_markup=maint_kb())
        return

    if data == "mrestart":
        await q.edit_message_text("🔄 بعيد تشغيل الشغالين...")
        bots = [b for b in manager.list_bots() if b.running]
        for b in bots:
            await asyncio.to_thread(b.restart)
        await q.edit_message_text(f"✅ عملت إعادة تشغيل لـ {len(bots)} بوت",
                                  parse_mode=ParseMode.HTML, reply_markup=maint_kb())
        return

    if data == "mstopall":
        count = 0
        for b in manager.list_bots():
            if b.running:
                await asyncio.to_thread(b.stop)
                count += 1
        await q.edit_message_text(f"⏹ وقفت {count} بوت",
                                  parse_mode=ParseMode.HTML, reply_markup=maint_kb())
        return

    if data == "msync":
        await q.edit_message_text("☁️ بمزامن كل البوتات للسحابة...")
        bots = manager.list_bots()
        for b in bots:
            await asyncio.to_thread(persist.push_bot_sync, b)
        await q.edit_message_text(f"☁️ اتزامن {len(bots)} بوت مع GitHub",
                                  parse_mode=ParseMode.HTML, reply_markup=maint_kb())
        return

    if data == "pbcast":
        PENDING_BCAST.add(uid)
        await q.edit_message_text(
            "📢 اكتب رسالة الإذاعة دلوقتي (أي نص — وهيتبعت لكل المستخدمين)",
            parse_mode=ParseMode.HTML)
        return

    if data == "bcast_go":
        await do_broadcast(q, ctx)
        return

    if data == "bcast_no":
        BCAST_DRAFT.pop(uid, None)
        await q.edit_message_text("❌ اتلغت", parse_mode=ParseMode.HTML)
        return

    if data.startswith("pbots_"):
        page = int(data.split("_")[1] or 0)
        txt, kb = bots_page_kb(page)
        await q.edit_message_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data.startswith("pbot_"):
        b = manager.get(data.split("_", 1)[1])
        if not b:
            await q.edit_message_text("⚠️ اتمسح")
            return
        txt, kb = bot_card(b, admin_view=True)
        await q.edit_message_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    # ---------- أزرار البوتات ----------
    action, _, bot_id = data.partition("_")
    b = manager.get(bot_id)
    if not b:
        await q.edit_message_text("⚠️ البوت ده اتمسح خلاص")
        return
    if not check_access(q, b):
        await q.answer("⛔️ ده مش بوتك!", show_alert=True)
        return

    if action in ("run", "inst") and not b.running and manager.running_count() >= MAX_RUNNING:
        await q.answer(f"⚠️ الحد {MAX_RUNNING} بوتات شغالة في نفس الوقت — قف واحدة الأول", show_alert=True)
        return

    if action == "run":
        await q.edit_message_text(f"⏳ بشغّل <b>{esc(b.meta['name'])}</b>...", parse_mode=ParseMode.HTML)
        if b.req_file.exists() and not b.libs_dir.exists():
            ok, out = await asyncio.to_thread(b.install)
            if not ok:
                await q.edit_message_text(
                    f"❌ فشل التثبيت:\n<code>{esc(out[-800:])}</code>", parse_mode=ParseMode.HTML)
                return
        await start_and_report(q, b)

    elif action == "rst":
        await q.edit_message_text(f"🔄 بيعيد تشغيل <b>{esc(b.meta['name'])}</b>...",
                                  parse_mode=ParseMode.HTML)
        if b.req_file.exists() and not b.libs_dir.exists():
            await asyncio.to_thread(b.install)
        ok, _ = b.restart()
        await asyncio.sleep(2)
        txt, kb = bot_card(b, admin_view=is_admin(uid))
        note = "\n\n🟢 <b>رجع يشتغل!</b>" if b.running else "\n\n⚠️ <b>وقع بعد التشغيل — شوف اللوج</b>"
        await q.edit_message_text(txt + note, parse_mode=ParseMode.HTML, reply_markup=kb)

    elif action == "inst":
        if b.id in INSTALLING:
            await q.answer("⏳ التثبيت شغال خلاص", show_alert=True)
            return
        INSTALLING.add(b.id)
        try:
            if not b.req_file.exists():
                pkgs = detect_packages(b.main_file.read_text(encoding="utf-8", errors="replace"))
                if pkgs:
                    b.req_file.write_text("\n".join(pkgs) + "\n", encoding="utf-8")
                    pkg_list = esc(", ".join(pkgs))
                else:
                    pkg_list = ""
            else:
                pkg_list = "المكتبات من requirements المحفوظ"
            await q.edit_message_text(
                f"📦 <b>بتثبّت لـ {esc(b.meta['name'])}...</b>\n<code>{pkg_list}</code>\n\n⏳ أقصى مدة 5 دقايق",
                parse_mode=ParseMode.HTML)
            ok, out = await asyncio.to_thread(b.install)
            if not ok:
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("جرّب تاني", callback_data=f"inst_{b.id}", style="primary"),
                    InlineKeyboardButton("هكتبها بنفسي", callback_data=f"waitreq_{b.id}"),
                ]])
                await q.edit_message_text(
                    f"❌ <b>فشل التثبيت:</b>\n<code>{esc(out[-700:])}</code>",
                    parse_mode=ParseMode.HTML, reply_markup=kb)
                return
            persist.push_bot_async(b)
            await start_and_report(q, b)
        finally:
            INSTALLING.discard(b.id)

    elif action == "waitreq":
        PENDING_REQS[uid] = b.id
        await q.edit_message_text(
            f"📦 ابعت دلوقتي <code>requirements.txt</code>\nوهيتثبت لـ <b>{esc(b.meta['name'])}</b>",
            parse_mode=ParseMode.HTML)

    elif action == "stop":
        await asyncio.to_thread(b.stop)
        txt, kb = bot_card(b, admin_view=is_admin(uid))
        await q.edit_message_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)

    elif action == "logs":
        log = b.logs()
        await q.message.reply_text(
            f"📜 <b>لوج {esc(b.meta['name'])}</b> (آخر 40 سطر):\n<code>{esc(log[-2500:])}</code>",
            parse_mode=ParseMode.HTML)

    elif action == "del":
        await asyncio.to_thread(b.delete)
        PENDING_REQS.pop(uid, None)
        await q.edit_message_text("🗑 البوت اتمسح (من السيرفر والسحابة)")


# ============================================================
# التشغيل
# ============================================================
def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ ناقص BOT_TOKEN")

    start_health_server()

    async def post_init(app):
        # قائمة أوامر نظيفة جنب مربع الكتابة
        await app.bot.set_my_commands([
            BotCommand("start", "القائمة الرئيسية"),
            BotCommand("mybots", "بوتاتك"),
            BotCommand("help", "الدليل"),
            BotCommand("id", "رقمك"),
            BotCommand("panel", "لوحة التحكم (أدمن)"),
        ])

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("mybots", cmd_mybots))
    app.add_handler(CommandHandler("panel", cmd_panel))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    restored = persist.pull_sync(str(Path("storage/bots")))
    print(f"☁️ استرجعت {restored} ملف من التخزين الدائم")

    def _resume():
        try:
            started = manager.restart_all()
            print(f"🔁 رجّعت شغل {len(started)} بوت: {started}")
        except Exception as e:
            print(f"⚠️ resume: {e}")
    threading.Thread(target=_resume, daemon=True).start()

    if RENDER_URL and app.job_queue:
        app.job_queue.run_repeating(self_ping, interval=600, first=120)
        print(f"🔄 self-ping على {RENDER_URL}")

    print("🤖 بوت هوست v3 شغال!")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
