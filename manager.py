# ============================================================
# مدير استضافة البوتات — تتبع حقيقي بالـ PID + تخزين دائم
# ============================================================
import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import persist

BASE_DIR = Path("storage")
BOTS_DIR = BASE_DIR / "bots"
BOTS_DIR.mkdir(parents=True, exist_ok=True)

INSTALL_TIMEOUT = 300      # أقصى مدة تثبيت مكتبات (ثواني)
MEM_LIMIT_MB = 150         # أقصى ذاكرة لبوت واحد
MAX_LOG_BYTES = 100_000    # أقصى حجم لوج محفوظ


def _child_limits():
    """حدود الموارد لبوتات المستخدمين."""
    try:
        mem = MEM_LIMIT_MB * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    except Exception:
        pass
    try:
        os.nice(5)
    except Exception:
        pass


def _pid_alive(pid):
    """هل العملية دي حية؟"""
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


class HostedBot:
    def __init__(self, bot_id, meta):
        self.id = bot_id
        self.meta = meta
        self.dir = BOTS_DIR / bot_id
        self.process = None  # بيتملي لو إحنا اللي شغلناه في الجلسة دي

    # ---------- مسارات ----------
    @property
    def main_file(self):
        return self.dir / self.meta.get("file", "main.py")

    @property
    def req_file(self):
        return self.dir / "requirements.txt"

    @property
    def libs_dir(self):
        return self.dir / "libs"

    @property
    def log_file(self):
        return self.dir / "log.txt"

    @property
    def meta_file(self):
        return self.dir / "meta.json"

    # ---------- حفظ وقراءة ----------
    def save_meta(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        self.meta_file.write_text(
            json.dumps(self.meta, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, bot_id):
        d = BOTS_DIR / bot_id
        mf = d / "meta.json"
        if not mf.exists():
            return None
        try:
            meta = json.loads(mf.read_text(encoding="utf-8"))
            return cls(bot_id, meta)
        except Exception:
            return None

    # ---------- التثبيت ----------
    def install(self):
        if not self.req_file.exists():
            return True, "مفيش requirements — اتشغّل من غير مكتبات"
        self.libs_dir.mkdir(exist_ok=True)
        try:
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install",
                 "--target", str(self.libs_dir.resolve()),
                 "--disable-pip-version-check", "--no-warn-script-location",
                 "-r", str(self.req_file.resolve())],
                capture_output=True, text=True, timeout=INSTALL_TIMEOUT,
            )
            ok = r.returncode == 0
            tail = (r.stdout + "\n" + r.stderr).strip().splitlines()
            return ok, "\n".join(tail[-15:])
        except subprocess.TimeoutExpired:
            return False, f"تثبيت المكتبات اخد وقت أطول من {INSTALL_TIMEOUT} ثانية واتوقف"

    # ---------- الحالة الحقيقية ----------
    @property
    def running(self):
        # إما إحنا شغلناه من شوية (في نفس الجلسة) أو فيه PID محفوظ لسه حي
        if self.process is not None:
            return self.process.poll() is None
        return _pid_alive(self.meta.get("pid"))

    # ---------- التشغيل ----------
    def start(self):
        if self.running:
            return False, "البوت شغال بالفعل"
        if not self.main_file.exists():
            return False, "ملف البوت مش موجود"

        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.libs_dir.resolve()) if self.libs_dir.exists() else ""
        env["PYTHONUNBUFFERED"] = "1"
        # نحقن رقم صاحب البوت كأدمن — بوتات كتير (متاجر وغيرها) بتقرا ADMIN_ID من البيئة
        owner = str(self.meta.get("owner_id") or "")
        if owner:
            env["ADMIN_ID"] = owner
            env["OWNER_ID"] = owner
            env["ADMIN"] = owner
        # نمسح أسرار البوت المضيف من بيئة البوتات المستضافة
        for k in ("BOT_TOKEN", "GH_PERSIST_TOKEN", "RENDER_API_KEY"):
            env.pop(k, None)

        log = open(self.log_file, "a", encoding="utf-8")
        log.write(f"\n===== تشغيل {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        log.flush()

        self.process = subprocess.Popen(
            [sys.executable, str(self.main_file.resolve())],
            stdout=log, stderr=subprocess.STDOUT,
            cwd=str(self.dir.resolve()), env=env,
            preexec_fn=_child_limits,
            start_new_session=True,
        )
        self.meta["pid"] = self.process.pid
        self.meta["auto_run"] = True
        self.save_meta()
        persist.push_bot_async(self)
        return True, "اتشغّل ✅"

    def stop(self):
        self.meta["auto_run"] = False
        pid = None
        if self.process is not None and self.process.poll() is None:
            pid = self.process.pid
        elif self.meta.get("pid"):
            pid = self.meta.get("pid")

        self.meta["pid"] = None
        self.save_meta()
        persist.push_bot_async(self)

        if not pid or not _pid_alive(pid):
            self.process = None
            return False, "البوت مش شغال أصلاً"

        # إيقاف مجموعة العملية كلها (البوت + أي أبناء له)
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except Exception:
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass

        # نستنى لحد 4 ثواني وبعدين قتل قسري
        for _ in range(20):
            if not _pid_alive(pid):
                break
            time.sleep(0.2)
        if _pid_alive(pid):
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except Exception:
                try:
                    os.kill(pid, signal.SIGKILL)
                except Exception:
                    pass
        self.process = None
        return True, "اتوقف ⏹"

    def delete(self):
        self.stop()
        shutil.rmtree(self.dir, ignore_errors=True)
        persist.delete_bot_async(self.id)

    # ---------- اللوج ----------
    def logs(self, lines=40):
        if not self.log_file.exists():
            return "(مفيش لوجات لسه)"
        try:
            with open(self.log_file, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - MAX_LOG_BYTES))
                data = f.read().decode("utf-8", errors="replace")
            return "\n".join(data.splitlines()[-lines:])
        except Exception as e:
            return f"(مشكلة في قراءة اللوج: {e})"


class BotManager:
    def __init__(self, max_bots=3):
        self.max_bots = max_bots

    def list_bots(self, owner_id=None):
        bots = []
        if not BOTS_DIR.exists():
            return bots
        for d in sorted(BOTS_DIR.iterdir()):
            if not d.is_dir():
                continue
            b = BotManager.get(d.name)
            if b and (owner_id is None or b.meta.get("owner_id") == owner_id):
                bots.append(b)
        return bots

    @staticmethod
    def get(bot_id):
        if not bot_id or not bot_id.replace("-", "").isalnum():
            return None
        return HostedBot.load(bot_id)

    def create(self, owner_id, owner_name, filename, content_bytes):
        count = len(self.list_bots(owner_id))
        total = len(self.list_bots())
        if total >= self.max_bots:
            return None, f"وصلنا الحد الأقصى للبوتات ({self.max_bots}) — امسح واحدة الأول"
        if count >= 2:
            return None, "الحد الأقصى ليك بوتين — امسح واحد لو عايز ترفع غيره"

        bot_id = f"b{int(time.time())}{owner_id % 1000}"
        bot = HostedBot(bot_id, {
            "name": filename,
            "owner_id": owner_id,
            "owner_name": owner_name,
            "file": "main.py",
            "pid": None,
            "auto_run": False,
        })
        bot.dir.mkdir(parents=True, exist_ok=True)
        bot.main_file.write_bytes(content_bytes)
        bot.save_meta()
        persist.push_bot_async(bot)
        return bot, None

    def restart_all(self):
        """إعادة تشغيل البوتات اللي كانت شغالة (مع تثبيت المكتبات لو ناقصة)."""
        started = []
        for b in self.list_bots():
            if not b.meta.get("auto_run") or not b.main_file.exists():
                continue
            try:
                if _pid_alive(b.meta.get("pid")):
                    continue  # شغال فعلاً — مفيش حاجة
                if b.req_file.exists() and not b.libs_dir.exists():
                    ok, _ = b.install()
                    if not ok:
                        continue
                ok, _ = b.start()
                if ok:
                    started.append(b.id)
            except Exception as e:
                print(f"⚠️ restart {b.id}: {e}")
        return started
