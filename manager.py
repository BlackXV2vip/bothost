# ============================================================
# مدير استضافة البوتات — بيخزن ويثبّت وشغّل بوتات المستخدمين
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
        os.nice(5)  # أولوية أقل من البوت الرئيسي
    except Exception:
        pass


class HostedBot:
    def __init__(self, bot_id, meta):
        self.id = bot_id
        self.meta = meta  # name, owner_id, owner_name, file, has_reqs, auto_run
        self.dir = BOTS_DIR / bot_id
        self.process = None

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
        self.meta_file.write_text(json.dumps(self.meta, ensure_ascii=False, indent=2), encoding="utf-8")

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
        """تثبيت المكتبات في مجلد libs خاص بالبوت."""
        if not self.req_file.exists():
            return True, "مفيش requirements — اتشغّل من غير مكتبات"
        self.libs_dir.mkdir(exist_ok=True)
        try:
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install",
                 "--target", str(self.libs_dir),
                 "--disable-pip-version-check", "--no-warn-script-location",
                 "-r", str(self.req_file)],
                capture_output=True, text=True, timeout=INSTALL_TIMEOUT,
            )
            ok = r.returncode == 0
            tail = (r.stdout + "\n" + r.stderr).strip().splitlines()
            return ok, "\n".join(tail[-15:])
        except subprocess.TimeoutExpired:
            return False, f"تثبيت المكتبات اخد وقت أطول من {INSTALL_TIMEOUT} ثانية واتوقف"

    # ---------- التشغيل ----------
    @property
    def running(self):
        return self.process is not None and self.process.poll() is None

    def start(self):
        if self.running:
            return False, "البوت شغال بالفعل"
        if not self.main_file.exists():
            return False, "ملف البوت مش موجود"

        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.libs_dir.resolve()) if self.libs_dir.exists() else ""
        env["PYTHONUNBUFFERED"] = "1"
        # نمسح توكن البوت المضيف عشان مايتسربش للبوتات المستضافة
        env.pop("BOT_TOKEN", None)

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
        self.meta["auto_run"] = True
        self.save_meta()
        return True, "اتشغّل ✅"

    def stop(self):
        self.meta["auto_run"] = False
        self.save_meta()
        if not self.running:
            self.process = None
            return False, "البوت مش شغال أصلاً"
        try:
            os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
        except Exception:
            try:
                self.process.kill()
            except Exception:
                pass
        self.process = None
        return True, "اتوقف ⏹"

    def delete(self):
        self.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

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
        for d in sorted(BOTS_DIR.iterdir()) if BOTS_DIR.exists() else []:
            b = BotManager.get(d.name)
            if b and (owner_id is None or b.meta.get("owner_id") == owner_id):
                bots.append(b)
        return bots

    @staticmethod
    def get(bot_id):
        # أمان: اسم المجلد حروف وأرقام بس
        if not bot_id.replace("-", "").isalnum():
            return None
        return HostedBot.load(bot_id)

    def create(self, owner_id, owner_name, filename, content_bytes):
        count = len([b for b in self.list_bots(owner_id)])
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
            "auto_run": False,
        })
        bot.dir.mkdir(parents=True, exist_ok=True)
        bot.main_file.write_bytes(content_bytes)
        bot.save_meta()
        return bot, None

    def restart_all(self):
        """إعادة تشغيل البوتات الشغالة (بعد إعادة تشغيل السيرفر)."""
        started = []
        for b in self.list_bots():
            if b.meta.get("auto_run") and b.main_file.exists():
                try:
                    ok, _ = b.start()
                    if ok:
                        started.append(b.id)
                except Exception:
                    pass
        return started
