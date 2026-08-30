# ============================================================
# مدير استضافة البوتات v3 — تتبع PID + تخزين دائم + مشاريع كاملة
# ============================================================
import io
import json
import os
import random
import resource
import shutil
import signal
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import persist

BASE_DIR = Path("storage")
BOTS_DIR = BASE_DIR / "bots"
BOTS_DIR.mkdir(parents=True, exist_ok=True)

INSTALL_TIMEOUT = 300      # أقصى مدة تثبيت مكتبات (ثواني)
MEM_LIMIT_MB = 150         # أقصى ذاكرة لبوت واحد
MAX_LOG_BYTES = 100_000    # أقصى حجم لوج محفوظ

# حدود مشاريع الـ zip
ZIP_MAX_FILES = 40
ZIP_MAX_TOTAL_MB = 15


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
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def mem_usage_mb(pid):
    """استهلاك الذاكرة الحقيقي للعملية من /proc."""
    try:
        with open(f"/proc/{int(pid)}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except Exception:
        pass
    return None


def system_memory_mb():
    """ذاكرة السيرفر الكلية والمتاحة."""
    total = avail = None
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total = round(int(line.split()[1]) / 1024)
                elif line.startswith("MemAvailable:"):
                    avail = round(int(line.split()[1]) / 1024)
    except Exception:
        pass
    return total, avail


def fmt_size(n):
    if n is None:
        return "?"
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n/1024:.1f} KB"
    return f"{n/1024/1024:.1f} MB"


def dir_size(path):
    total = 0
    try:
        for p in Path(path).rglob("*"):
            if p.is_file():
                total += p.stat().st_size
    except Exception:
        pass
    return total


def unpack_zip(data: bytes, dest: Path):
    """
    استخراج آمن لمشروع مضغوط (zip) متعدد الملفات.
    بيرجع (اسم ملف التشغيل، هل فيه requirements.txt).
    """
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = [i for i in zf.infolist() if not i.is_dir()]
    if len(names) > ZIP_MAX_FILES:
        raise ValueError(f"الملف فيه {len(names)} عنصر — الحد {ZIP_MAX_FILES}")
    if sum(i.file_size for i in names) > ZIP_MAX_TOTAL_MB * 1024 * 1024:
        raise ValueError(f"محتوى الضغط أكبر من {ZIP_MAX_TOTAL_MB} ميجا")

    # لو كل الملفات جوه مجلد واحد — نشيل البادئة
    tops = {n.filename.split("/")[0] for n in names}
    strip = ""
    if len(tops) == 1 and "/" in names[0].filename:
        strip = list(tops)[0] + "/"

    dest.mkdir(parents=True, exist_ok=True)
    py_files, reqs = [], False
    for info in names:
        rel = info.filename[len(strip):] if strip and info.filename.startswith(strip) else info.filename
        rel = rel.lstrip("/")
        if not rel or ".." in rel.split("/"):
            continue  # حماية من path traversal
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(zf.read(info))
        if rel == "requirements.txt":
            reqs = True
        elif rel.endswith(".py"):
            py_files.append(rel)

    if not py_files:
        raise ValueError("مفيش أي ملف .py جوه الضغط!")

    # ملف التشغيل: main.py ثم bot.py ثم app.py ثم run.py ثم أول .py
    for cand in ("main.py", "bot.py", "app.py", "run.py"):
        if cand in py_files:
            return cand, reqs
    return sorted(py_files)[0], reqs


class HostedBot:
    def __init__(self, bot_id, meta):
        self.id = bot_id
        self.meta = meta
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

    # ---------- معلومات ----------
    @property
    def running(self):
        if self.process is not None:
            return self.process.poll() is None
        return _pid_alive(self.meta.get("pid"))

    def mem_mb(self):
        pid = None
        if self.process is not None and self.process.poll() is None:
            pid = self.process.pid
        elif self.running:
            pid = self.meta.get("pid")
        return mem_usage_mb(pid) if pid else None

    def size_bytes(self):
        return dir_size(self.dir)

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

    # ---------- التشغيل ----------
    def start(self):
        if self.running:
            return False, "البوت شغال بالفعل"
        if not self.main_file.exists():
            return False, "ملف البوت مش موجود"

        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.libs_dir.resolve()) if self.libs_dir.exists() else ""
        env["PYTHONUNBUFFERED"] = "1"
        # نحقن رقم صاحب البوت كأدمن لبوتاته
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
        self.meta["starts"] = self.meta.get("starts", 0) + 1
        self.meta["last_start"] = time.strftime("%Y-%m-%d %H:%M")
        self.save_meta()
        persist.push_bot_async(self)
        return True, "اتشغّل ✅"

    def restart(self):
        """إعادة تشغيل: إيقاف إن كان شغال ثم تشغيل."""
        was = self.running
        if was:
            self.stop()
            time.sleep(1)
        return self.start()

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

        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except Exception:
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass

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
    def __init__(self, max_bots=8, max_running=4, per_user=3):
        self.max_bots = max_bots
        self.max_running = max_running
        self.per_user = per_user

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

    def running_count(self):
        return sum(1 for b in self.list_bots() if b.running)

    def users_count(self):
        return len({b.meta.get("owner_id") for b in self.list_bots()})

    @staticmethod
    def get(bot_id):
        if not bot_id or not bot_id.replace("-", "").isalnum():
            return None
        return HostedBot.load(bot_id)

    def allocate(self, owner_id, owner_name, display_name):
        """فحص الحدود وتجهيز بوت جديد (من غير ملفات — الملف اللي بعدها)."""
        count = len(self.list_bots(owner_id))
        total = len(self.list_bots())
        if total >= self.max_bots:
            return None, f"السعة ممتلئة ({self.max_bots} بوت) — امسح واحدة أو كلم الأدمن"
        if count >= self.per_user:
            return None, f"الحد الأقصى ليك {self.per_user} بوتات — امسح واحد لو عايز ترفع غيره"

        bot_id = f"b{int(time.time())}{owner_id % 1000}{random.randint(10,99)}"
        bot = HostedBot(bot_id, {
            "name": display_name,
            "owner_id": owner_id,
            "owner_name": owner_name,
            "file": "main.py",
            "pid": None,
            "auto_run": False,
            "starts": 0,
            "created": time.strftime("%Y-%m-%d %H:%M"),
        })
        bot.dir.mkdir(parents=True, exist_ok=True)
        return bot, None

    def create(self, owner_id, owner_name, filename, content_bytes):
        bot, err = self.allocate(owner_id, owner_name, filename)
        if err:
            return None, err
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
                    continue
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
