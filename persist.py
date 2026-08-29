# ============================================================
# التخزين الدائم — بيحفظ بوتات المستخدمين على ريبو جيت هاب خاص
# عشان خطة Render المجانية قرصها مؤقت وبيفضي مع كل إعادة نشر
# ============================================================
import base64
import os
import threading
from pathlib import Path

import httpx

TOKEN = os.environ.get("GH_PERSIST_TOKEN", "")
REPO = os.environ.get("GH_PERSIST_REPO", "")  # مثال: BlackXV2vip/bothost-storage
API = "https://api.github.com"
BRANCH = "main"
ENABLED = bool(TOKEN and REPO)

# الملفات اللي بنتزامنها لكل بوت
KEEP_FILES = ("meta.json", "requirements.txt")


def _headers():
    return {"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"}


def _put_file(path, content_bytes, msg):
    """رفع/تحديث ملف في الريبو (بيجيب sha لو موجود)."""
    content = base64.b64encode(content_bytes).decode()
    sha = None
    r = httpx.get(f"{API}/repos/{REPO}/contents/{path}", headers=_headers(), timeout=30)
    if r.status_code == 200:
        sha = r.json().get("sha")
    data = {"message": msg, "content": content, "branch": BRANCH}
    if sha:
        data["sha"] = sha
    r = httpx.put(f"{API}/repos/{REPO}/contents/{path}", headers=_headers(), json=data, timeout=60)
    return r.status_code in (200, 201)


def _delete_file(path):
    r = httpx.get(f"{API}/repos/{REPO}/contents/{path}", headers=_headers(), timeout=30)
    if r.status_code != 200:
        return True
    sha = r.json().get("sha")
    data = {"message": f"delete {path}", "sha": sha, "branch": BRANCH}
    r = httpx.request("DELETE", f"{API}/repos/{REPO}/contents/{path}",
                      headers=_headers(), json=data, timeout=60)
    return r.status_code == 200


def _bot_main_file(bot_dir, meta):
    return meta.get("file", "main.py")


def push_bot_sync(bot):
    """رفع ملفات بوت واحد للريبو (متزامن)."""
    if not ENABLED:
        return
    files = [bot.meta.get("file", "main.py"), *KEEP_FILES]
    for rel in files:
        p = bot.dir / rel
        if not p.exists():
            continue
        try:
            _put_file(f"bots/{bot.id}/{rel}", p.read_bytes(), f"sync {bot.id}/{rel}")
        except Exception as e:
            print(f"⚠️ persist push {bot.id}/{rel}: {e}")


def push_bot_async(bot):
    if ENABLED:
        threading.Thread(target=push_bot_sync, args=(bot,), daemon=True).start()


def delete_bot_sync(bot_id):
    if not ENABLED:
        return
    try:
        r = httpx.get(f"{API}/repos/{REPO}/git/trees/HEAD?recursive=1",
                      headers=_headers(), timeout=60)
        tree = r.json().get("tree", [])
        for item in tree:
            if item.get("type") == "blob" and item["path"].startswith(f"bots/{bot_id}/"):
                _delete_file(item["path"])
    except Exception as e:
        print(f"⚠️ persist delete {bot_id}: {e}")


def delete_bot_async(bot_id):
    if ENABLED:
        threading.Thread(target=delete_bot_sync, args=(bot_id,), daemon=True).start()


def pull_sync(bots_dir):
    """
    استرجاع كل البوتات من الريبو للمجلد المحلي.
    بيرجع عدد الملفات اللي اترجعت (0 لو التخزين مقفول أو فاضي).
    """
    if not ENABLED:
        return 0
    n = 0
    try:
        r = httpx.get(f"{API}/repos/{REPO}/git/trees/HEAD?recursive=1",
                      headers=_headers(), timeout=60)
        r.raise_for_status()
        tree = r.json().get("tree", [])
        for item in tree:
            if item.get("type") != "blob":
                continue
            path = item["path"]  # bots/<id>/<file>
            if not path.startswith("bots/"):
                continue
            f = httpx.get(f"{API}/repos/{REPO}/contents/{path}", headers=_headers(), timeout=60)
            if f.status_code != 200:
                continue
            data = base64.b64decode(f.json().get("content", ""))
            dest = Path(bots_dir) / path[len("bots/"):]
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            n += 1
    except Exception as e:
        print(f"⚠️ persist pull: {e}")
    return n
